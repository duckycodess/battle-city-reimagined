"""Where the opt-in gimmick terrain would put a tank, as pure arithmetic.

The rules engine owns *whether* a displacement happens: it walks tanks in ascending
identifier order and tests every candidate body against the bodies it has already moved.
This module owns *where* the candidate is, which is the half that has nothing to do with
occupancy and everything to do with the grid. Splitting it that way is what lets the bot
package predict a push or an arrival with the engine's own arithmetic instead of a second
copy of it; see :mod:`battle_city_ai.perception`.

Every function here is a pure function of a grid, a pixel position and the rules. None of
them looks at a tank, a projectile or another body, and none of them decides anything: a
returned position is a *proposal* the caller still has to collision-check with its own
blocking test. ``None`` means "this terrain has nothing to say", never "blocked".

Centre-cell activation
----------------------
A tank is ``rules.tank_size`` pixels square and a tile is ``rules.tile_size`` pixels
square, and in the shipped rules both are 16. A 16px body therefore sits wholly inside one
16px tile only when it is exactly tile-aligned, which a 2px-per-tick mover almost never
is. Asking which tile a tank is *on* has to be a question about one point, and the point
is the body's centre: :func:`centre_cell`. The earlier draft of this change asked for the
whole body to stay inside the tile after a 2px push, which no 16px body can do; the
accepted proposal replaced it with this rule for that reason.
"""

from __future__ import annotations

from .geometry import GridPos, Vec2, cell_of, clamp
from .rules import Rules
from .tiles import Tile, TileGrid, conveyor_direction

TELEPORT_PAIR_SIZE: int = 2
"""How many teleport pads a valid stage declares, when it declares any at all.

Zero or exactly two. One pad has no destination and three have no unambiguous pairing, so
:meth:`battle_city_sim.stage.Stage.create` and the content loader both refuse any other
count before a run starts. :func:`teleport_pads` reports ``None`` for anything else rather
than inventing a pairing for a grid that was built without passing either gate.
"""


def centre_cell(position: Vec2, rules: Rules) -> GridPos:
    """Return the grid cell holding the centre of a tank body whose top-left is ``position``.

    Integer arithmetic throughout: the half-size is a floor division, so an odd
    ``tank_size`` biases the centre up and left by half a pixel, consistently, on every
    machine.
    """
    half = rules.tank_size // 2
    return cell_of(Vec2(position.x + half, position.y + half), rules.tile_size)


def conveyor_target(
    grid: TileGrid, position: Vec2, cell: GridPos, world: tuple[int, int], rules: Rules
) -> Vec2 | None:
    """Return where a conveyor at ``cell`` would push a body at ``position``.

    ``cell`` is the centre cell the tank *started* its movement phase in, not the one it
    ends in: entering a conveyor part-way through a tick does not activate it until the
    next tick, and leaving one conveyor for another does not chain. ``world`` is the
    ``(width, height)`` of the playfield in pixels.

    Returns ``None`` only when ``cell`` is off the grid or is not a conveyor. A belt that
    runs into the world edge still returns a target -- the clamped one, equal to
    ``position`` -- because the caller treats a displacement that moves nothing as a
    refused one, exactly as it does for a commanded step into the edge.
    """
    if not grid.contains(cell):
        return None
    direction = conveyor_direction(grid.at(cell))
    if direction is None:
        return None
    width, height = world
    dx, dy = direction.scaled(rules.tank_speed)
    return Vec2(
        clamp(position.x + dx, 0, width - rules.tank_size),
        clamp(position.y + dy, 0, height - rules.tank_size),
    )


def teleport_pads(grid: TileGrid) -> tuple[GridPos, GridPos] | None:
    """Return the stage's pad pair in row-major order, or ``None`` when it has none.

    The pairing is read off the grid every time rather than stored, because it is a
    property of the terrain and nothing may change it: a pad is not in
    :data:`~battle_city_sim.tiles.PROJECTILE_TILE_DAMAGE`, so no tick can create, destroy
    or move one. That is also why no pad state is serialised -- there is none -- and why
    the canonical state layout is unchanged by this feature.

    A grid carrying any count other than zero or :data:`TELEPORT_PAIR_SIZE` yields
    ``None``. Both gates that build a playable stage reject such a grid first; answering
    ``None`` here means a hand-built state with a malformed pad count simply has no
    teleports rather than an arbitrary pairing.
    """
    cells = grid.positions_of(Tile.TELEPORT_PAD)
    if len(cells) != TELEPORT_PAIR_SIZE:
        return None
    return (cells[0], cells[1])


def teleport_target(
    position: Vec2,
    cell: GridPos,
    pads: tuple[GridPos, GridPos],
    rules: Rules,
) -> Vec2 | None:
    """Return where a body at ``position`` standing on pad ``cell`` would arrive.

    The arrival keeps the body's pixel offset from the pad cell's own origin, so a tank
    straddling the top edge of one pad straddles the top edge of the other. That offset is
    signed and is deliberately not clamped: clamping it would snap a straddling tank onto
    the destination cell, which is a teleport that also silently moves the tank sideways.

    Returns ``None`` when ``cell`` is not one of ``pads``. The caller still has to
    collision-check the result; an offset that carries the body off the playfield produces
    a position whose cells are out of bounds, which the caller's blocking test refuses.
    """
    first, second = pads
    if cell == first:
        destination = second
    elif cell == second:
        destination = first
    else:
        return None
    size = rules.tile_size
    offset_x = position.x - cell.x * size
    offset_y = position.y - cell.y * size
    return Vec2(destination.x * size + offset_x, destination.y * size + offset_y)
