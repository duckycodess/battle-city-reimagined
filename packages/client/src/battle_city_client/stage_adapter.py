"""Turn a validated content :class:`~battle_city_content.Level` into a simulation stage.

The content package deliberately stops at the level record: it owns the JSON format and
its validation, and it never builds a :class:`~battle_city_sim.Stage` because it may not
depend on the simulation. The client owns the join, and this module is the whole of it.

The mapping is mechanical. Both packages already agree on the classic vocabulary -- a
16x16 grid, tile-code characters ``"0"``-``"8"``, integer ``x``/``y`` spawn cells and a
single home tile -- so the adapter renames record types and hands the rows to
:meth:`Stage.create`, which validates them a second time against the simulation's own
contract. Passing that second gate is the point: a level that loads but could not be
played is reported here, by level identifier and origin path, rather than at tick zero.

Nothing in this module reads a clock, a display or the environment, and
:func:`stage_from_level` touches no file: it is a pure function of the level record it is
given. :func:`bundled_stage_catalog` is the one entry point that reaches the filesystem,
and it does so only through the content loader.

Waves are not translated. ``Level.waves`` is carried by the content record, but the
simulation has no wave scheduler and this phase does not invent one: enemy cadence is
campaign policy and belongs to a later phase with accepted gameplay rules. Dropping the
field here is explicit rather than accidental.
"""

from __future__ import annotations

from dataclasses import dataclass

from battle_city_content import ContentError, GridCell, Level, Pack, load_bundled_pack
from battle_city_protocol import ContentRef
from battle_city_sim import GridPos, PlayerSpawn, Stage, StageValidationError


class StageAdapterError(RuntimeError):
    """A validated level could not be expressed as a simulation stage.

    The message names the level and the file it came from, because the level is data a
    pack author wrote and the fix belongs in that file.
    """


@dataclass(frozen=True, slots=True)
class StageEntry:
    """One selectable stage: the simulation stage plus the labels the menu shows."""

    level_id: str
    name: str
    stage: Stage

    @property
    def player_slots(self) -> tuple[int, ...]:
        """Player slots the stage declares, in ascending order."""
        return tuple(spawn.slot for spawn in self.stage.player_spawns)


def cell_to_grid_pos(cell: GridCell) -> GridPos:
    """Rename a content cell to a simulation cell. The coordinate systems are identical."""
    return GridPos(x=cell.x, y=cell.y)


def stage_from_level(level: Level) -> Stage:
    """Build the simulation stage for ``level``.

    Raises :class:`StageAdapterError` when the simulation rejects the layout, naming the
    level identifier, its origin file and the simulation's own complaint.
    """
    try:
        return Stage.create(
            stage_id=level.level_id,
            name=level.name,
            rows=level.grid.rows,
            player_spawns=tuple(
                PlayerSpawn(slot=spawn.slot, cell=cell_to_grid_pos(spawn.cell))
                for spawn in level.player_spawns
            ),
            enemy_spawns=tuple(cell_to_grid_pos(cell) for cell in level.enemy_spawns),
        )
    except StageValidationError as error:
        raise StageAdapterError(
            f"{level.level_id} ({level.origin}): {error}",
        ) from error


def content_ref_for(pack: Pack, level: Level) -> ContentRef:
    """Describe ``level`` inside ``pack`` as the reference peers compare on.

    The server builds the same record from the same two fields, which is the point: a
    lobby accepts a client when the two agree about the pack, and a session accepts it
    when the two agree about the level as well.
    """
    return ContentRef(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        level_id=level.level_id,
        content_schema_version=level.schema_version,
    )


def bundled_content_ref(level_id: str | None = None) -> ContentRef:
    """The content reference for a bundled level, named or the first one.

    This is what an online client claims by default. A lobby running a pack this build
    does not ship refuses the join by name, which is the honest failure: the fix is to
    install the pack, not to let the client in and desynchronise later.
    """
    try:
        pack = load_bundled_pack()
    except ContentError as error:
        raise StageAdapterError(f"bundled pack: {error}") from error
    level = pack.levels[0] if level_id is None else pack.level(level_id)
    return content_ref_for(pack, level)


def stage_catalog(pack: Pack) -> tuple[StageEntry, ...]:
    """Adapt every level in ``pack``, in manifest order.

    Adaptation is all-or-nothing, matching how the content loader reads a pack: a single
    unplayable level fails the whole catalog instead of silently shortening the menu.
    """
    return tuple(
        StageEntry(level_id=level.level_id, name=level.name, stage=stage_from_level(level))
        for level in pack.levels
    )


def bundled_stage_catalog() -> tuple[StageEntry, ...]:
    """Load the bundled classic pack and adapt it.

    This is the only function here that reads the filesystem. A content failure is
    re-raised as :class:`StageAdapterError` so a caller that is wiring up a menu has one
    exception type to report, with the loader's file-and-field message preserved.
    """
    try:
        pack = load_bundled_pack()
    except ContentError as error:
        raise StageAdapterError(f"bundled pack: {error}") from error
    return stage_catalog(pack)
