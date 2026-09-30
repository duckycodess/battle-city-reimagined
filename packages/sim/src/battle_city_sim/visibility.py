"""Explicit visibility rules for the forest overlay.

Concealment is derived, not stored: it is a pure question about a state, so a renderer,
a bot, and a server all compute the same answer from the same snapshot without another
field in the canonical encoding.

The rule is deliberately strict. A tank is concealed only while **every** tile its body
overlaps conceals, so a tank straddling a forest edge stays visible. The historical
runtime had no concealment rule at all: it simply drew the forest sprites after the
tanks, so coverage was whatever the draw order produced. Stating the rule here makes it
testable and keeps a future AI from inheriting a renderer's accident.

Concealment currently has no effect on movement, collision or damage. Giving it a
gameplay effect, for instance hiding tanks from bots, needs a proposal that states the
AI, network and accessibility consequences.
"""

from __future__ import annotations

from .entities import Tank
from .geometry import cells_overlapping
from .rules import DEFAULT_RULES, Rules
from .state import SimulationState
from .tiles import TILES_CONCEALING_TANKS, TileGrid


def is_tank_concealed(grid: TileGrid, tank: Tank, rules: Rules = DEFAULT_RULES) -> bool:
    """Return whether every tile under ``tank`` conceals it."""
    cells = cells_overlapping(tank.body(rules), rules.tile_size)
    if not cells:
        return False
    for cell in cells:
        if not grid.contains(cell):
            return False
        if grid.at(cell) not in TILES_CONCEALING_TANKS:
            return False
    return True


def concealed_tank_ids(state: SimulationState, rules: Rules = DEFAULT_RULES) -> tuple[int, ...]:
    """Return the identifiers of concealed tanks, in ascending identifier order."""
    return tuple(
        tank.entity_id
        for tank in sorted(state.tanks, key=lambda item: item.entity_id)
        if is_tank_concealed(state.grid, tank, rules)
    )
