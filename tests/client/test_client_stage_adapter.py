"""The Level-to-Stage adapter: mechanical, validated, and free of I/O."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from battle_city_client.stage_adapter import (
    StageAdapterError,
    bundled_stage_catalog,
    cell_to_grid_pos,
    resolve_pack,
    stage_catalog,
    stage_from_level,
)
from battle_city_content import GridCell, Wave, bundled_content_root, load_bundled_pack
from battle_city_sim import CLASSIC_GRID_SIZE, GridPos, Tile
from client_helpers import make_level, make_pack, rows_with

BUNDLED_IDS = ("classic-01", "classic-02", "classic-03")


def test_bundled_catalog_adapts_every_classic_stage() -> None:
    catalog = bundled_stage_catalog()
    assert tuple(entry.level_id for entry in catalog) == BUNDLED_IDS
    for entry in catalog:
        assert entry.stage.stage_id == entry.level_id
        assert entry.stage.grid.width == CLASSIC_GRID_SIZE
        assert entry.stage.grid.height == CLASSIC_GRID_SIZE


def test_adapter_preserves_the_level_it_was_given() -> None:
    """Every field the simulation stage carries comes straight from the level record."""
    for level in load_bundled_pack().levels:
        stage = stage_from_level(level)
        assert stage.grid.to_rows() == level.grid.rows
        assert stage.name == level.name
        assert stage.base_cell == cell_to_grid_pos(level.base_cell)
        assert stage.enemy_spawns == tuple(cell_to_grid_pos(cell) for cell in level.enemy_spawns)
        assert tuple(spawn.slot for spawn in stage.player_spawns) == tuple(
            spawn.slot for spawn in level.player_spawns
        )
        assert tuple(spawn.cell for spawn in stage.player_spawns) == tuple(
            cell_to_grid_pos(spawn.cell) for spawn in level.player_spawns
        )


def test_adapter_is_a_pure_function_of_its_argument() -> None:
    """Called twice on one record it produces equal stages and reads nothing outside."""
    level = make_level()
    assert stage_from_level(level) == stage_from_level(level)


def test_cell_conversion_keeps_the_coordinate_system() -> None:
    assert cell_to_grid_pos(GridCell(x=3, y=11)) == GridPos(x=3, y=11)


def test_declared_waves_do_not_reach_the_stage() -> None:
    """Wave data is carried by content and deliberately dropped here.

    The simulation has no wave scheduler and this phase does not add one, so a level that
    declares waves must adapt to exactly the same stage as one that does not. If that
    ever stops being true, someone has started scheduling enemies in the client.
    """
    plain = make_level()
    with_waves = make_level(waves=(Wave(enemies=4), Wave(enemies=6)))
    assert stage_from_level(with_waves) == stage_from_level(plain)


def test_a_level_the_simulation_rejects_names_itself_and_its_file() -> None:
    """Two home tiles load as content but cannot be a stage."""
    broken = make_level(
        rows=rows_with({(7, 15): Tile.HOME, (3, 3): Tile.HOME}),
        level_id="two-bases",
        origin=Path("/fixture/two-bases.json"),
    )
    with pytest.raises(StageAdapterError) as failure:
        stage_from_level(broken)
    message = str(failure.value)
    assert "two-bases" in message
    assert "two-bases.json" in message
    assert "home base" in message


def test_a_spawn_inside_terrain_is_rejected() -> None:
    """The simulation refuses a tank that would start inside a wall; so does the client."""
    broken = make_level(rows=rows_with({(7, 15): Tile.HOME, (7, 12): Tile.BRICK}))
    with pytest.raises(StageAdapterError):
        stage_from_level(broken)


def test_catalog_adaptation_is_all_or_nothing() -> None:
    """One unplayable level fails the pack rather than silently shortening the menu."""
    good = make_level(level_id="good")
    broken = replace(
        make_level(level_id="broken"),
        grid=make_level(rows=rows_with({})).grid,
    )
    with pytest.raises(StageAdapterError) as failure:
        stage_catalog(make_pack((good, broken)))
    assert "broken" in str(failure.value)


def test_catalog_keeps_manifest_order() -> None:
    pack = make_pack((make_level(level_id="second"), make_level(level_id="first")))
    assert tuple(entry.level_id for entry in stage_catalog(pack)) == ("second", "first")


def test_entry_reports_the_slots_a_stage_declares() -> None:
    catalog = stage_catalog(make_pack((make_level(player_cells=((7, 12), (9, 12))),)))
    assert catalog[0].player_slots == (1, 2)


# -- what a manifest path's levels are relative to ----------------------------


def _write_external_pack(manifest_dir: Path, *, levels_dir: Path) -> Path:
    """An external pack: a manifest in ``manifest_dir`` naming ``levels/<id>.json``.

    ``levels_dir`` is where the level file is actually written, so a test can state the
    layout it means rather than inferring it from the manifest's own directory.
    """
    manifest_dir.mkdir(parents=True, exist_ok=True)
    levels_dir.mkdir(parents=True, exist_ok=True)
    (levels_dir / "outside-01.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "outside-01",
                "name": "Outside Stage",
                "grid": {"width": 16, "height": 16, "rows": list(rows_with({(7, 15): Tile.HOME}))},
                "spawns": {
                    "players": [{"slot": 1, "x": 7, "y": 12}],
                    "enemies": [{"x": 1, "y": 1}],
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = manifest_dir / "outside.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "outside",
                "version": "1.0.0",
                "name": "Outside pack",
                "content_schema_version": 1,
                "authors": ["tests"],
                "license": {"spdx_id": "NOASSERTION", "notice": "fixture"},
                "levels": [{"id": "outside-01", "path": "levels/outside-01.json"}],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_an_external_manifest_resolves_its_levels_beside_itself(tmp_path: Path) -> None:
    """The loader's documented default, which an outside pack is entitled to rely on."""
    manifest = _write_external_pack(tmp_path / "mine", levels_dir=tmp_path / "mine" / "levels")
    assert tuple(level.level_id for level in resolve_pack(str(manifest)).levels) == ("outside-01",)


def test_a_directory_called_packs_does_not_move_an_external_manifests_root(
    tmp_path: Path,
) -> None:
    """The regression: the directory's *name* said nothing about where the levels were.

    ``/foo/packs/outside.json`` saying ``levels/outside-01.json`` means
    ``/foo/packs/levels``, exactly as it would under any other directory name. Reading
    the name and resolving against ``/foo`` instead rejected a pack that was never wrong.
    """
    root = tmp_path / "packs"
    manifest = _write_external_pack(root, levels_dir=root / "levels")
    assert tuple(level.level_id for level in resolve_pack(str(manifest)).levels) == ("outside-01",)


def test_a_bundled_manifest_named_by_absolute_path_keeps_the_shared_content_root(
    tmp_path: Path,
) -> None:
    """The packaged layout really does split ``packs/`` from ``levels/``.

    A player who types the installed manifest's own path reaches the same pack the
    bundled identifier does, because the root follows from the manifest being inside the
    packaged content rather than from how it was spelled.
    """
    absolute = bundled_content_root() / "packs" / "gimmick-demo.json"
    assert absolute.is_file()
    by_path = resolve_pack(str(absolute))
    by_identifier = resolve_pack("gimmick-demo")
    assert by_path.pack_id == by_identifier.pack_id
    assert by_path.level_ids == by_identifier.level_ids


def test_a_manifest_that_is_neither_a_file_nor_an_identifier_says_so(tmp_path: Path) -> None:
    with pytest.raises(StageAdapterError, match="neither a pack manifest"):
        resolve_pack(str(tmp_path / "nothing-here.json"))
