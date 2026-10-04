"""Authoritative sessions on gimmick terrain, and the version gate around them.

Two claims, both about authority.

The server owns every terrain-driven displacement. A conveyor push and a teleport arrival
are decided by the tick the server steps and reported in the snapshot it broadcasts, and a
client that sent nothing still sees its tank move. Nothing a client can say chooses an
outcome, because the only things a client can say are a facing, a shot and a respawn.

And a session only speaks terrain its content reference defines. ``ContentRef``'s content
schema version is the sole compatibility signal for this terrain -- the wire version, the
snapshot version and the canonical state version are all unchanged -- so a session whose
stage carries codes its reference does not cover is refused while it is being described,
not discovered on the first keyframe.
"""

from __future__ import annotations

import pytest
from battle_city_protocol import DirectionCode, JoinAccepted, RejectionCode, StateSnapshot
from battle_city_server import (
    GameSession,
    Reply,
    ServerConfigurationError,
    UnspeakableTerrainError,
    grid_rows,
    require_speakable_terrain,
)
from battle_city_sim import (
    DEFAULT_RULES,
    GridPos,
    PlayerSpawn,
    Stage,
    Tank,
    Tile,
    centre_cell,
    tile_code_char,
)
from server_helpers import build_rows, content_ref, join_request, make_config, move

TILE = DEFAULT_RULES.tile_size
SPEED = DEFAULT_RULES.tank_speed
BELT = GridPos(4, 11)
SOURCE_PAD = GridPos(12, 11)
PARTNER_PAD = GridPos(12, 4)
TICKS_ONTO_THE_CELL_BELOW = TILE // SPEED // 2
"""Ticks of held movement that carry a cell-aligned tank's centre into the next cell."""


def gimmick_stage() -> Stage:
    """Each slot spawns one cell above the terrain it is going to drive onto.

    Spawns stay on empty ground, as the stage contract requires: a spawn on a belt or a
    pad would displace or transport a tank on the tick it entered play, which is a start
    nobody authored.
    """
    overrides = {
        BELT: Tile.CONVEYOR_E,
        SOURCE_PAD: Tile.TELEPORT_PAD,
        PARTNER_PAD: Tile.TELEPORT_PAD,
    }
    return Stage.create(
        stage_id="test-stage",
        name="Test Stage",
        rows=build_rows(overrides),
        player_spawns=[
            PlayerSpawn(slot=1, cell=GridPos(BELT.x, BELT.y - 1)),
            PlayerSpawn(slot=2, cell=GridPos(SOURCE_PAD.x, SOURCE_PAD.y - 1)),
        ],
        enemy_spawns=[GridPos(2, 1)],
    )


def gimmick_session() -> GameSession:
    return GameSession(
        make_config(stage=gimmick_stage(), content=content_ref(content_schema_version=2))
    )


def joined(session: GameSession, slot: int) -> int:
    connection = session.connect()
    request = join_request(slot, content=session.content)
    replies = session.handle(connection, request)
    assert isinstance(replies[0].message, JoinAccepted), replies[0].message
    return connection


def drive(session: GameSession, connection: int, slot: int, ticks: int) -> None:
    """Hold DOWN for ``ticks`` ticks, the way a player leaning on a key does."""
    for index in range(ticks):
        session.handle(connection, move(slot, index + 1, DirectionCode.DOWN))
        session.advance_tick()


def tank_of(session: GameSession, slot: int) -> Tank:
    player = session.state.find_player(slot)
    assert player is not None and player.tank_id is not None
    return session.state.tank(player.tank_id)


def snapshots(replies: tuple[Reply, ...]) -> tuple[StateSnapshot, ...]:
    return tuple(reply.message for reply in replies if isinstance(reply.message, StateSnapshot))


# -- the version gate ---------------------------------------------------------


def test_a_classic_stage_is_speakable_in_both_versions() -> None:
    rows = build_rows()
    require_speakable_terrain(rows, 1)
    require_speakable_terrain(rows, 2)


def test_gimmick_terrain_is_refused_under_content_version_one() -> None:
    rows = gimmick_stage().grid.to_rows()
    require_speakable_terrain(rows, 2)
    with pytest.raises(UnspeakableTerrainError) as caught:
        require_speakable_terrain(rows, 1)
    # The first offending cell in row-major order is the partner pad on row 4.
    assert tile_code_char(Tile.TELEPORT_PAD) in str(caught.value)
    assert "grid.rows[4][12]" in str(caught.value)
    assert "version 1 does not define" in str(caught.value)


def test_an_unsupported_content_version_is_refused_by_name() -> None:
    with pytest.raises(UnspeakableTerrainError, match="this build speaks 1, 2"):
        require_speakable_terrain(build_rows(), 7)


def test_a_session_claiming_version_one_over_gimmick_terrain_will_not_start() -> None:
    """Refused while the session is described, not discovered on the first keyframe."""
    with pytest.raises(ServerConfigurationError, match="does not fit its content reference"):
        make_config(stage=gimmick_stage(), content=content_ref(content_schema_version=1))


def test_a_session_declaring_version_two_starts() -> None:
    session = gimmick_session()
    assert session.content.content_schema_version == 2
    assert session.state.tick == 0


def test_a_client_claiming_the_wrong_content_version_is_refused() -> None:
    session = gimmick_session()
    connection = session.connect()
    request = join_request(1, content=content_ref(content_schema_version=1))
    replies = session.handle(connection, request)
    assert len(replies) == 1
    rejection = replies[0].message
    assert getattr(rejection, "code", None) is RejectionCode.CONTENT_MISMATCH


# -- what a keyframe carries --------------------------------------------------


def test_a_keyframe_carries_the_gimmick_codes_unchanged() -> None:
    session = gimmick_session()
    snapshot = session.snapshot(keyframe=True)
    assert snapshot.grid is not None
    rows = snapshot.grid
    assert rows[BELT.y][BELT.x] == tile_code_char(Tile.CONVEYOR_E)
    assert rows[SOURCE_PAD.y][SOURCE_PAD.x] == tile_code_char(Tile.TELEPORT_PAD)
    assert rows[PARTNER_PAD.y][PARTNER_PAD.x] == tile_code_char(Tile.TELEPORT_PAD)
    assert all(len(row) == 16 for row in rows)


def test_a_row_is_never_widened_by_a_two_digit_tile_value() -> None:
    """``Tile.CONVEYOR_E`` is value 10; a derived character would make a 17-column row."""
    assert str(Tile.CONVEYOR_E.value) == "10"
    assert all(len(row) == 16 for row in grid_rows(gimmick_session().state))


def test_the_published_versions_are_unchanged_by_this_terrain() -> None:
    gimmick = gimmick_session().snapshot(keyframe=True)
    classic = GameSession(make_config()).snapshot(keyframe=True)
    assert gimmick.snapshot_version == classic.snapshot_version
    assert gimmick.state_version == classic.state_version
    assert gimmick.protocol_version == classic.protocol_version


def test_a_classic_session_still_sends_only_classic_codes() -> None:
    """The regression that matters on the wire: classic rows did not move."""
    rows = grid_rows(GameSession(make_config()).state)
    assert rows == build_rows()
    assert set("".join(rows)) <= set("012345678")


# -- authority ----------------------------------------------------------------


def test_the_server_moves_a_belt_rider_that_sent_nothing() -> None:
    session = gimmick_session()
    connection = joined(session, 1)
    joined(session, 2)
    drive(session, connection, 1, TICKS_ONTO_THE_CELL_BELOW)

    riding = tank_of(session, 1)
    assert centre_cell(riding.position, DEFAULT_RULES) == BELT
    before = riding.position

    session.advance_tick()  # no input from anybody
    after = tank_of(session, 1)
    assert after.position.x == before.x + SPEED
    assert after.position.y == before.y
    assert after.facing is riding.facing


def test_the_server_decides_a_teleport_the_client_never_asked_for() -> None:
    session = gimmick_session()
    joined(session, 1)
    connection = joined(session, 2)
    drive(session, connection, 2, TICKS_ONTO_THE_CELL_BELOW)

    arrived = tank_of(session, 2)
    assert centre_cell(arrived.position, DEFAULT_RULES) == PARTNER_PAD
    assert arrived.position.x == SOURCE_PAD.x * TILE


def test_a_broadcast_snapshot_reports_the_position_the_server_reached() -> None:
    session = gimmick_session()
    connection = joined(session, 1)
    joined(session, 2)
    drive(session, connection, 1, TICKS_ONTO_THE_CELL_BELOW)

    replies = session.advance_tick()
    tank = tank_of(session, 1)
    broadcast = snapshots(replies)
    assert broadcast
    for snapshot in broadcast:
        reported = next(item for item in snapshot.tanks if item.slot == 1)
        assert (reported.x, reported.y) == (tank.position.x, tank.position.y)
        assert snapshot.state_hash == session.snapshot(keyframe=False).state_hash


def test_a_rider_keeps_moving_while_the_client_stays_silent() -> None:
    """The belt is the server's; a client that stops sending does not stop being carried."""
    session = gimmick_session()
    connection = joined(session, 1)
    joined(session, 2)
    drive(session, connection, 1, TICKS_ONTO_THE_CELL_BELOW)

    positions = []
    for _ in range(3):
        session.advance_tick()
        positions.append(tank_of(session, 1).position.x)
    assert positions == [positions[0], positions[0] + SPEED, positions[0] + 2 * SPEED]
