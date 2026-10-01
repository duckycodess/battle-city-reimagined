"""``python -m battle_city_tools``: what each command prints, and what it exits with."""

from __future__ import annotations

from pathlib import Path

import pytest
from battle_city_content import GridCell, TileCode, load_level
from battle_city_tools import BUNDLED_ROOT, LevelDraft
from battle_city_tools.cli import EXIT_INVALID, EXIT_OK, EXIT_REFUSED, main
from battle_city_tools.export import MANIFEST_NAME
from tools_helpers import make_draft, write_level


def test_validate_accepts_the_bundled_pack(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", "--bundled"]) == EXIT_OK
    assert "ok: pack classic" in capsys.readouterr().out


def test_validate_accepts_a_single_level(capsys: pytest.CaptureFixture[str]) -> None:
    level = BUNDLED_ROOT / "levels" / "classic-01.json"
    assert main(["validate", "--level", str(level)]) == EXIT_OK
    assert "ok: level classic-01" in capsys.readouterr().out


def test_validate_reports_an_invalid_level_by_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    draft = make_draft()
    draft.paint(GridCell(7, 15), TileCode.EMPTY)
    path = write_level(tmp_path / "no-base.json", draft)
    assert main(["validate", "--level", str(path)]) == EXIT_INVALID
    error = capsys.readouterr().err
    assert str(path) in error
    assert "grid.rows" in error
    assert "exactly one home base" in error


def test_validate_reports_a_pack_that_names_a_missing_level(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "pack.json"
    manifest.write_text(
        '{"schema_version": 1, "id": "p", "version": "1.0.0", "name": "P", '
        '"content_schema_version": 1, "authors": ["t"], '
        '"license": {"spdx_id": "NOASSERTION", "notice": "n"}, '
        '"levels": [{"id": "gone", "path": "levels/gone.json"}]}',
        encoding="utf-8",
    )
    assert main(["validate", "--pack", str(manifest)]) == EXIT_INVALID
    assert "cannot be read" in capsys.readouterr().err


def test_inspect_describes_a_pack(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", "--bundled"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "pack classic 1.0.0" in out
    assert "level classic-01" in out
    assert "home" in out


def test_inspect_describes_one_level(capsys: pytest.CaptureFixture[str]) -> None:
    level = BUNDLED_ROOT / "levels" / "classic-02.json"
    assert main(["inspect", "--level", str(level)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "level classic-02" in out
    assert "base            (7, 15)" in out


def test_inspect_counts_every_tile_in_the_vocabulary(capsys: pytest.CaptureFixture[str]) -> None:
    level = BUNDLED_ROOT / "levels" / "classic-01.json"
    main(["inspect", "--level", str(level)])
    out = capsys.readouterr().out
    for tile in TileCode:
        assert tile.name.lower() in out


def test_export_writes_a_loadable_pack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    destination = tmp_path / "out"
    assert main(["export", "--bundled", "--output", str(destination)]) == EXIT_OK
    assert "exported classic" in capsys.readouterr().out
    assert main(["validate", "--pack", str(destination / MANIFEST_NAME)]) == EXIT_OK


def test_export_refuses_an_existing_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "out"
    main(["export", "--bundled", "--output", str(destination)])
    capsys.readouterr()
    assert main(["export", "--bundled", "--output", str(destination)]) == EXIT_REFUSED
    assert "refused" in capsys.readouterr().err
    assert main(["export", "--bundled", "--output", str(destination), "--overwrite"]) == EXIT_OK


def test_export_does_not_offer_a_single_level_source(tmp_path: Path) -> None:
    """A level is not a pack, and the parser says so rather than the command."""
    level = BUNDLED_ROOT / "levels" / "classic-01.json"
    with pytest.raises(SystemExit) as raised:
        main(["export", "--level", str(level), "--output", str(tmp_path / "out")])
    assert raised.value.code == 2


def test_create_writes_a_blank_level_that_validates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fresh.json"
    assert (
        main(["create", "--id", "fresh-one", "--name", "Fresh One", "--output", str(target)])
        == EXIT_OK
    )
    assert "created fresh-one" in capsys.readouterr().out
    assert load_level(target).level_id == "fresh-one"


def test_create_refuses_an_existing_file_then_accepts_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "fresh.json"
    arguments = ["create", "--id", "fresh-one", "--name", "Fresh One", "--output", str(target)]
    assert main(arguments) == EXIT_OK
    assert main(arguments) == EXIT_REFUSED
    assert main([*arguments, "--overwrite"]) == EXIT_OK


def test_create_refuses_to_write_inside_the_bundled_pack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = BUNDLED_ROOT / "levels" / "intruder.json"
    status = main(
        ["create", "--id", "intruder", "--name", "X", "--output", str(target), "--overwrite"]
    )
    assert status == EXIT_REFUSED
    assert "bundled content root" in capsys.readouterr().err
    assert not target.exists()


def test_create_rejects_an_identifier_the_schema_forbids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Identity is content, so a bad identifier is a validation failure with a field."""
    target = tmp_path / "bad.json"
    assert (
        main(["create", "--id", "Not Valid", "--name", "X", "--output", str(target)])
        == EXIT_INVALID
    )
    assert "id" in capsys.readouterr().err
    assert not target.exists()


def test_a_source_is_required() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["validate"])
    assert raised.value.code == 2


def test_two_sources_are_refused() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["validate", "--bundled", "--level", "x.json"])
    assert raised.value.code == 2


def test_a_pack_root_can_be_given_separately(tmp_path: Path) -> None:
    """A manifest in ``packs/`` beside a ``levels/`` directory needs their shared parent."""
    root = tmp_path / "pack"
    (root / "packs").mkdir(parents=True)
    (root / "levels").mkdir()
    draft = LevelDraft.blank(level_id="rooted", name="Rooted")
    write_level(root / "levels" / "rooted.json", draft)
    (root / "packs" / "pack.json").write_text(
        '{"schema_version": 1, "id": "rooted-pack", "version": "1.0.0", "name": "R", '
        '"content_schema_version": 1, "authors": ["t"], '
        '"license": {"spdx_id": "NOASSERTION", "notice": "n"}, '
        '"levels": [{"id": "rooted", "path": "levels/rooted.json"}]}',
        encoding="utf-8",
    )
    arguments = ["validate", "--pack", str(root / "packs" / "pack.json")]
    assert main(arguments) == EXIT_INVALID
    assert main([*arguments, "--root", str(root)]) == EXIT_OK
