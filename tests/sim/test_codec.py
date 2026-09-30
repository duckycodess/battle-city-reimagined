"""Canonical encoding and hashing."""

from __future__ import annotations

from dataclasses import replace

from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    PlayerSpawn,
    PowerupKind,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    Stage,
    TankVariant,
    TickInput,
    Tile,
    encode_state,
    new_game,
    run_ticks,
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


# --------------------------------------------------------------------------------------
# Golden vectors
#
# The assertions above are all relative: two in-process runs agree, and a change changes
# the hash. Nothing in them pins the encoding itself, so a silent change to field order,
# an integer width, or an enum member value would still pass. codec.py states that such a
# change needs a proposal and a replay-compatibility policy; these vectors are what makes
# that policy enforceable. Updating them is the signal that the policy applies.
#
# The fixture is written out here rather than taken from helpers so that a test-helper
# edit cannot move the golden values underneath it.
# --------------------------------------------------------------------------------------

GOLDEN_ROWS = (
    "2222222222222222",
    "2000000000000002",
    "2000300000040002",
    "2002220220222002",
    "2000000000000002",
    "2020002112002002",
    "2005002002000202",
    "2020002112002002",
    "2007000000000202",
    "2020002002002002",
    "2110000000000112",
    "2020202002020202",
    "2020202002020202",
    "2000000000000002",
    "2000002220000002",
    "2222222822222222",
)
GOLDEN_SEED = 20260101
GOLDEN_STAGE_ID = "golden-01"


def _golden_stage() -> Stage:
    return Stage.create(
        stage_id=GOLDEN_STAGE_ID,
        name="Golden",
        rows=GOLDEN_ROWS,
        player_spawns=[PlayerSpawn(slot=1, cell=GridPos(14, 11))],
        enemy_spawns=[GridPos(1, 1), GridPos(14, 1)],
    )


def _golden_script(player_id: int) -> tuple[TickInput, ...]:
    return (
        TickInput.of(0, SpawnEnemyCommand(GridPos(1, 1), TankVariant.ENEMY_SHIELDED)),
        TickInput.of(1, MoveCommand(player_id, Direction.UP), FireCommand(player_id)),
        TickInput.of(2, MoveCommand(player_id, Direction.UP)),
        TickInput.of(3, MoveCommand(2, Direction.DOWN), FireCommand(2)),
        TickInput.of(4, MoveCommand(player_id, Direction.LEFT)),
        TickInput.of(5, SpawnPowerupCommand(GridPos(13, 13), PowerupKind.GATLING)),
        TickInput.of(6, MoveCommand(player_id, Direction.DOWN)),
        TickInput.of(7, MoveCommand(2, Direction.RIGHT)),
    )


GOLDEN_INITIAL_LENGTH = 375
GOLDEN_INITIAL_PREFIX = "424353494d000100000000000000000009676f6c64656e2d3031001000100202"
GOLDEN_INITIAL_HASH = "15c14edc9a53d4e85a0b534eea445ecb11cc0569689f3113bb69cf21af691a5a"
GOLDEN_REPLAY_HASH = "7ff3047e41f0e8f2f367176e0ef9796efd703c6763cc5cf741f579610248b3c3"


def test_the_tick_zero_encoding_matches_its_golden_vector() -> None:
    encoded = encode_state(new_game(_golden_stage(), seed=GOLDEN_SEED))
    assert len(encoded) == GOLDEN_INITIAL_LENGTH
    assert encoded[:32].hex() == GOLDEN_INITIAL_PREFIX
    assert state_hash(new_game(_golden_stage(), seed=GOLDEN_SEED)) == GOLDEN_INITIAL_HASH


def test_a_replayed_script_ends_on_its_golden_hash() -> None:
    state = new_game(_golden_stage(), seed=GOLDEN_SEED)
    final, _ = run_ticks(state, _golden_script(state.tanks[0].entity_id))
    assert final.tick == len(_golden_script(state.tanks[0].entity_id))
    assert state_hash(final) == GOLDEN_REPLAY_HASH
