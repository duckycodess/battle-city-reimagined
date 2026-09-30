"""Canonical encoding and hashing."""

from __future__ import annotations

from dataclasses import replace

from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    PowerupKind,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TankVariant,
    Tile,
    encode_state,
    state_hash,
)
from helpers import make_stage, make_state, run

PLAYER_ID = 1


def test_the_encoding_is_self_describing() -> None:
    encoded = encode_state(make_state())
    assert encoded.startswith(b"BCSIM")
    assert encoded[5:7] == CANONICAL_STATE_VERSION.to_bytes(2, "big")


def test_the_hash_is_a_sha256_hex_digest() -> None:
    digest = state_hash(make_state())
    assert len(digest) == 64
    assert digest == digest.lower()
    assert set(digest) <= set("0123456789abcdef")


def test_equal_states_encode_identically() -> None:
    assert encode_state(make_state()) == encode_state(make_state())
    assert state_hash(make_state()) == state_hash(make_state())


def test_the_seed_is_part_of_the_canonical_state() -> None:
    assert state_hash(make_state(seed=1)) != state_hash(make_state(seed=2))


def test_moving_a_tank_changes_the_hash() -> None:
    before = make_state()
    after, _ = run(before, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert state_hash(after) != state_hash(before)


def test_damaging_a_tile_changes_the_hash() -> None:
    stage = make_stage(overrides={GridPos(8, 8): Tile.BRICK})
    before = make_state(stage)
    after, _ = run(before, 10, commands={0: [FireCommand(PLAYER_ID)]})
    assert after.grid.at(GridPos(8, 8)) is Tile.CRACKED_BRICK
    assert state_hash(after) != state_hash(before)


def test_the_encoding_does_not_depend_on_tuple_order() -> None:
    """The codec sorts by identifier, so a differently ordered tuple encodes the same."""
    state, _ = run(
        make_state(),
        2,
        commands={
            0: [
                SpawnEnemyCommand(GridPos(4, 4), TankVariant.ENEMY_NORMAL),
                SpawnEnemyCommand(GridPos(6, 6), TankVariant.ENEMY_SHIELDED),
                SpawnPowerupCommand(GridPos(2, 2), PowerupKind.GATLING),
            ],
            1: [FireCommand(PLAYER_ID)],
        },
    )
    shuffled = replace(
        state,
        tanks=tuple(reversed(state.tanks)),
        powerups=tuple(reversed(state.powerups)),
        players=tuple(reversed(state.players)),
    )
    assert encode_state(shuffled) == encode_state(state)


def test_stage_identity_is_part_of_the_canonical_state() -> None:
    one = make_state(make_stage(stage_id="stage-one"))
    two = make_state(make_stage(stage_id="stage-two"))
    assert state_hash(one) != state_hash(two)
