"""Real input driving the real simulation: movement, firing, terrain damage.

Nothing here stubs the simulation. Each test states a stage, drives a session with the
same intents the keyboard produces and asserts what the rules engine did, so a client
change that quietly started deciding a rule shows up as a failing assertion about the
rule rather than about the client.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from battle_city_client import Action, PlayerIntent, StageSession
from battle_city_client.shell import Screen
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    RespawnCommand,
    Tile,
    TileDamaged,
    state_hash,
)
from client_helpers import make_entry, make_shell, make_stage, observed

TILE = DEFAULT_RULES.tile_size


def _session(**kwargs: object) -> StageSession:
    stage = make_stage(**kwargs)  # type: ignore[arg-type]
    return StageSession.start(stage)


def test_a_new_session_places_one_player_tank_facing_up() -> None:
    session = StageSession.start(make_stage())
    tank = session.player_tank
    assert tank is not None
    assert tank.facing is Direction.UP
    assert tank.position.x == 7 * TILE
    assert tank.position.y == 12 * TILE
    assert session.player.lives == DEFAULT_RULES.starting_lives


def test_holding_a_direction_moves_at_the_rules_speed() -> None:
    session = StageSession.start(make_stage())
    start = session.player_tank
    assert start is not None
    session = session.advance(5, PlayerIntent(direction=Direction.LEFT))
    moved = session.player_tank
    assert moved is not None
    assert moved.facing is Direction.LEFT
    assert moved.position.x == start.position.x - 5 * DEFAULT_RULES.tank_speed
    assert moved.position.y == start.position.y


def test_terrain_blocks_the_tank_where_the_simulation_says_it_does() -> None:
    """Stone one cell to the left stops the tank against it and no further."""
    stage = make_stage({(5, 12): Tile.STONE})
    session = StageSession.start(stage)
    session = session.advance(40, PlayerIntent(direction=Direction.LEFT))
    tank = session.player_tank
    assert tank is not None
    assert tank.position.x == 6 * TILE


def test_firing_damages_brick_then_removes_it() -> None:
    """Brick becomes cracked brick, cracked brick becomes empty. Two shots, two events."""
    stage = make_stage({(7, 10): Tile.BRICK})
    session = StageSession.start(stage)
    damaged: list[TileDamaged] = []
    for _ in range(60):
        session = session.advance(1, PlayerIntent(fire=True))
        damaged.extend(event for event in session.last_events if isinstance(event, TileDamaged))
        if len(damaged) >= 2:
            break
    assert [(event.previous, event.current) for event in damaged[:2]] == [
        (Tile.BRICK, Tile.CRACKED_BRICK),
        (Tile.CRACKED_BRICK, Tile.EMPTY),
    ]
    assert session.state.grid.at(GridPos(7, 10)) is Tile.EMPTY


def test_a_shot_is_reflected_by_a_mirror() -> None:
    """The mirror tables are the simulation's; the client only has to let a shot reach one."""
    stage = make_stage({(7, 10): Tile.MIRROR_SE})
    session = StageSession.start(stage)
    session = session.advance(1, PlayerIntent(fire=True))
    for _ in range(12):
        session = session.advance(1)
        if session.state.projectiles and session.state.projectiles[0].reflected_cell:
            break
    assert session.state.projectiles
    shot = session.state.projectiles[0]
    assert shot.reflected_cell == GridPos(7, 10)
    assert shot.direction is Direction.RIGHT


def test_the_projectile_limit_is_the_simulation_gate_not_a_client_filter() -> None:
    """Fire is sent every tick; the rules engine decides how many shots exist."""
    session = StageSession.start(make_stage())
    session = session.advance(6, PlayerIntent(fire=True))
    assert len(session.state.projectiles) <= DEFAULT_RULES.max_projectiles_per_tank


def test_intent_becomes_exactly_the_commands_it_should() -> None:
    session = StageSession.start(make_stage())
    tank = session.player_tank
    assert tank is not None
    assert session.commands_for(PlayerIntent()) == ()
    assert session.commands_for(PlayerIntent(direction=Direction.DOWN, fire=True)) == (
        MoveCommand(tank_id=tank.entity_id, direction=Direction.DOWN),
        FireCommand(tank_id=tank.entity_id),
    )


def test_a_slot_waiting_to_respawn_asks_at_the_first_legal_tick() -> None:
    """Built by clearing the slot's tank, which is what a destroyed player leaves behind."""
    session = StageSession.start(make_stage())
    tank = session.player_tank
    assert tank is not None
    state = session.state
    emptied = replace(
        state,
        tanks=(),
        players=(replace(state.players[0], tank_id=None),),
    )
    waiting = replace(session, state=emptied)
    assert waiting.commands_for(PlayerIntent()) == (RespawnCommand(slot=1),)
    assert waiting.advance(1).player_tank is not None


def test_no_respawn_is_asked_for_while_the_spawn_cell_is_occupied() -> None:
    """The simulation refuses that respawn by raising; the client must not provoke it."""
    session = StageSession.start(make_stage())
    state = session.state
    blocked = replace(state, players=(replace(state.players[0], tank_id=None),))
    waiting = replace(session, state=blocked)
    assert waiting.commands_for(PlayerIntent()) == ()
    assert waiting.advance(1).state.tick == blocked.tick + 1


def test_an_eliminated_slot_stops_asking() -> None:
    session = StageSession.start(make_stage())
    state = session.state
    done = replace(
        state,
        tanks=(),
        players=(replace(state.players[0], tank_id=None, lives=0),),
    )
    assert replace(session, state=done).commands_for(PlayerIntent()) == ()


def test_advancing_zero_ticks_changes_nothing_and_negative_ticks_are_refused() -> None:
    session = StageSession.start(make_stage())
    assert session.advance(0).state == session.state
    with pytest.raises(ValueError, match="must not be negative"):
        session.advance(-1)


def test_restarting_returns_to_tick_zero_with_the_same_seed() -> None:
    session = StageSession.start(make_stage(), seed=99)
    advanced = session.advance(20, PlayerIntent(fire=True))
    restarted = advanced.restarted()
    assert restarted.seed == 99
    assert state_hash(restarted.state) == state_hash(session.state)


def test_frame_chunking_cannot_change_the_run() -> None:
    """The same total ticks, delivered in different shapes, produce the same state.

    This is the property the accumulator exists to protect, checked here against the real
    simulation rather than against a tick count.
    """
    intent = PlayerIntent(direction=Direction.LEFT, fire=True)
    stage = make_stage({(4, 12): Tile.BRICK})

    even = StageSession.start(stage)
    for _ in range(30):
        even = even.advance(2, intent)

    lumpy = StageSession.start(stage)
    for ticks in (5, 1, 12, 0, 7, 3, 20, 12):
        lumpy = lumpy.advance(ticks, intent)

    assert even.state.tick == lumpy.state.tick == 60
    assert state_hash(even.state) == state_hash(lumpy.state)


def test_a_shell_driven_through_the_menu_plays_the_real_simulation() -> None:
    shell = make_shell((make_entry(make_stage({(7, 10): Tile.BRICK})),))
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.PLAYING
    for _ in range(60):
        shell.advance(1, PlayerIntent(fire=True))
    assert shell.session is not None
    assert shell.session.state.grid.at(GridPos(7, 10)) is not Tile.BRICK
