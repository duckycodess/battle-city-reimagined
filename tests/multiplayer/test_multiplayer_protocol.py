"""The lobby wire format: round trips, bounds, direction, and what it refuses to carry."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from battle_city_protocol import (
    COMPETITIVE_MODES,
    MATCH_SETTINGS_VERSION,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_LOBBY_MEMBERS,
    MAX_TEAM,
    PROTOCOL_VERSION,
    ClientMessage,
    ContentRef,
    LobbyConfigure,
    LobbyInfo,
    LobbyJoin,
    LobbyLeave,
    LobbyMember,
    LobbyReady,
    LobbyStart,
    LobbyState,
    LobbyWelcome,
    MatchMode,
    MatchSettings,
    MatchStarting,
    Message,
    MessageError,
    RejectionCode,
    ServerMessage,
    SessionInfo,
    TeamAssignment,
    decode_client_message,
    decode_server_message,
    encode_message,
)

SESSION_ID = "match-1"
TICKET = "ticket-host-aaaaaaaa"
TOKEN = "token-host-aaaaaaaa"
CONTENT = ContentRef(
    pack_id="duo-pack", pack_version="1.0.0", level_id="duo-arena", content_schema_version=1
)


def settings(mode: MatchMode = MatchMode.COOP) -> MatchSettings:
    return MatchSettings(
        mode=mode, level_id="duo-arena", content=CONTENT, tick_rate=60, max_players=2
    )


def lobby_info() -> LobbyInfo:
    return LobbyInfo(
        capacity=2,
        offered_modes=tuple(MatchMode),
        playable_modes=(MatchMode.COOP,),
        offered_levels=("duo-arena",),
    )


def session_info() -> SessionInfo:
    return SessionInfo(
        tick_rate=60,
        keyframe_interval=30,
        max_players=2,
        content=CONTENT,
        rules_digest="a" * 64,
        state_version=1,
    )


def members() -> tuple[LobbyMember, ...]:
    return (
        LobbyMember(slot=1, display_name="host", ready=True, host=True, connected=True),
        LobbyMember(slot=2, display_name="guest", ready=False, host=False, connected=True, team=2),
    )


CLIENT_MESSAGES: list[ClientMessage] = [
    LobbyJoin(session_id=SESSION_ID, ticket=TICKET, display_name="host", content=CONTENT),
    LobbyJoin(session_id=SESSION_ID, ticket=TICKET, display_name="host", content=CONTENT, team=3),
    LobbyConfigure(
        session_id=SESSION_ID, slot=1, revision=2, mode=MatchMode.COOP, level_id="duo-arena"
    ),
    LobbyConfigure(
        session_id=SESSION_ID,
        slot=1,
        revision=2,
        mode=MatchMode.TEAM_BATTLE,
        level_id="duo-arena",
        teams=(TeamAssignment(slot=1, team=1), TeamAssignment(slot=2, team=2)),
    ),
    LobbyReady(session_id=SESSION_ID, slot=2, revision=2, ready=True),
    LobbyStart(session_id=SESSION_ID, slot=1, revision=2),
    LobbyLeave(session_id=SESSION_ID, slot=2),
]

SERVER_MESSAGES: list[ServerMessage] = [
    LobbyWelcome(session_id=SESSION_ID, slot=1, host=True, lobby=lobby_info()),
    LobbyState(
        session_id=SESSION_ID,
        revision=2,
        settings=settings(),
        members=members(),
        host_slot=1,
        startable=False,
        blocked=RejectionCode.MEMBERS_NOT_READY,
    ),
    LobbyState(
        session_id=SESSION_ID,
        revision=3,
        settings=settings(),
        members=members(),
        host_slot=1,
        startable=True,
    ),
    MatchStarting(
        session_id=SESSION_ID,
        slot=1,
        token=TOKEN,
        session=session_info(),
        settings=settings(),
    ),
]


def document(message: Message) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(encode_message(message))
    return decoded


def reencode(body: dict[str, Any]) -> bytes:
    return json.dumps(body).encode()


# -- round trips ---------------------------------------------------------------


@pytest.mark.parametrize("message", CLIENT_MESSAGES, ids=lambda m: type(m).__name__)
def test_lobby_client_messages_round_trip(message: ClientMessage) -> None:
    assert decode_client_message(encode_message(message)) == message


@pytest.mark.parametrize("message", SERVER_MESSAGES, ids=lambda m: type(m).__name__)
def test_lobby_server_messages_round_trip(message: ServerMessage) -> None:
    assert decode_server_message(encode_message(message)) == message


def test_every_lobby_message_declares_type_version_and_session() -> None:
    for message in [*CLIENT_MESSAGES, *SERVER_MESSAGES]:
        body = document(message)
        assert body["protocol_version"] == PROTOCOL_VERSION
        assert body["session_id"] == SESSION_ID
        assert isinstance(body["type"], str)


def test_the_protocol_version_moved_for_the_lobby() -> None:
    """A version 1 peer must fail with an upgrade message, not an unknown type."""
    assert PROTOCOL_VERSION == 2
    body = document(CLIENT_MESSAGES[0])
    body["protocol_version"] = 1
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.PROTOCOL_VERSION_UNSUPPORTED


# -- direction and strictness --------------------------------------------------


@pytest.mark.parametrize("message", SERVER_MESSAGES, ids=lambda m: type(m).__name__)
def test_a_server_lobby_message_is_not_a_client_message(message: ServerMessage) -> None:
    with pytest.raises(MessageError) as error:
        decode_client_message(encode_message(message))
    assert error.value.code is RejectionCode.UNEXPECTED_MESSAGE


@pytest.mark.parametrize("message", CLIENT_MESSAGES, ids=lambda m: type(m).__name__)
def test_a_client_lobby_message_is_not_a_server_message(message: ClientMessage) -> None:
    with pytest.raises(MessageError) as error:
        decode_server_message(encode_message(message))
    assert error.value.code is RejectionCode.UNEXPECTED_MESSAGE


@pytest.mark.parametrize("message", CLIENT_MESSAGES, ids=lambda m: type(m).__name__)
def test_an_unknown_field_is_refused_not_ignored(message: ClientMessage) -> None:
    body = document(message)
    body["admin"] = True
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.UNKNOWN_FIELD


def test_an_unknown_nested_field_is_refused() -> None:
    body = document(SERVER_MESSAGES[1])
    body["members"][0]["token"] = TOKEN
    with pytest.raises(MessageError) as error:
        decode_server_message(reencode(body))
    assert error.value.code is RejectionCode.UNKNOWN_FIELD


def test_an_unknown_mode_is_refused() -> None:
    body = document(CLIENT_MESSAGES[2])
    body["mode"] = "deathmatch"
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_settings_version_this_build_does_not_read_is_refused() -> None:
    body = document(SERVER_MESSAGES[1])
    body["settings"]["settings_version"] = MATCH_SETTINGS_VERSION + 1
    with pytest.raises(MessageError) as error:
        decode_server_message(reencode(body))
    assert error.value.code is RejectionCode.PROTOCOL_VERSION_UNSUPPORTED


# -- bounds --------------------------------------------------------------------


def test_a_display_name_is_bounded_and_plain() -> None:
    with pytest.raises(MessageError):
        LobbyJoin(
            session_id=SESSION_ID,
            ticket=TICKET,
            display_name="x" * (MAX_DISPLAY_NAME_LENGTH + 1),
            content=CONTENT,
        )
    with pytest.raises(MessageError):
        LobbyJoin(session_id=SESSION_ID, ticket=TICKET, display_name="two words", content=CONTENT)


def test_a_team_is_bounded() -> None:
    with pytest.raises(MessageError):
        TeamAssignment(slot=1, team=MAX_TEAM + 1)
    with pytest.raises(MessageError):
        TeamAssignment(slot=1, team=0)


def test_a_roster_is_bounded_and_names_each_slot_once() -> None:
    crowd = tuple(
        LobbyMember(
            slot=1 + index, display_name=f"p{index}", ready=False, host=False, connected=True
        )
        for index in range(MAX_LOBBY_MEMBERS)
    )
    with pytest.raises(MessageError):
        LobbyState(
            session_id=SESSION_ID,
            revision=0,
            settings=settings(),
            members=crowd + crowd[:1],
            host_slot=1,
            startable=False,
        )
    duplicated = (crowd[0], crowd[0])
    with pytest.raises(MessageError) as error:
        LobbyState(
            session_id=SESSION_ID,
            revision=0,
            settings=settings(),
            members=duplicated,
            host_slot=1,
            startable=False,
        )
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_ticket_is_held_to_the_token_rules() -> None:
    with pytest.raises(MessageError):
        LobbyJoin(session_id=SESSION_ID, ticket="short", display_name="host", content=CONTENT)


def test_teams_have_no_meaning_outside_a_team_mode() -> None:
    with pytest.raises(MessageError) as error:
        LobbyConfigure(
            session_id=SESSION_ID,
            slot=1,
            revision=0,
            mode=MatchMode.COOP,
            level_id="duo-arena",
            teams=(TeamAssignment(slot=1, team=1),),
        )
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_startable_lobby_may_not_also_name_a_blocking_reason() -> None:
    with pytest.raises(MessageError):
        LobbyState(
            session_id=SESSION_ID,
            revision=0,
            settings=settings(),
            members=members(),
            host_slot=1,
            startable=True,
            blocked=RejectionCode.MEMBERS_NOT_READY,
        )


def test_a_lobby_may_not_advertise_a_playable_mode_it_does_not_offer() -> None:
    with pytest.raises(MessageError):
        LobbyInfo(
            capacity=2,
            offered_modes=(MatchMode.COOP,),
            playable_modes=(MatchMode.TEAM_BATTLE,),
            offered_levels=("duo-arena",),
        )


# -- what the lobby must never carry -------------------------------------------


def test_no_broadcast_lobby_message_has_anywhere_to_put_a_credential() -> None:
    """The roster goes to everyone, so a field a token could travel in is a leak."""
    secret_names = {"token", "ticket", "secret", "password", "credential"}
    for message in (SERVER_MESSAGES[0], SERVER_MESSAGES[1], SERVER_MESSAGES[2]):
        names = {field.name for field in dataclasses.fields(message)}
        assert not names & secret_names, type(message).__name__
    roster_fields = {field.name for field in dataclasses.fields(members()[0])}
    assert not roster_fields & secret_names


def test_the_credential_message_is_the_only_one_that_carries_a_token() -> None:
    carriers = [
        type(message).__name__
        for message in SERVER_MESSAGES
        if "token" in {field.name for field in dataclasses.fields(message)}
    ]
    assert carriers == ["MatchStarting"]


def test_a_lobby_client_message_carries_no_authoritative_field() -> None:
    forbidden = {"score", "seed", "rng", "state", "state_hash", "damage", "grid", "tanks"}
    for message in CLIENT_MESSAGES:
        names = {field.name for field in dataclasses.fields(message)}
        assert not names & forbidden


def test_a_client_cannot_name_its_own_slot_when_asking_for_a_seat() -> None:
    """Seating is the server's decision; the ticket says which seat, not the message."""
    names = {field.name for field in dataclasses.fields(CLIENT_MESSAGES[0])}
    assert "slot" not in names


def test_cheats_cannot_be_enabled_in_a_competitive_configuration() -> None:
    for mode in COMPETITIVE_MODES:
        with pytest.raises(MessageError) as error:
            MatchSettings(
                mode=mode,
                level_id="duo-arena",
                content=CONTENT,
                tick_rate=60,
                max_players=2,
                cheats_enabled=True,
            )
        assert error.value.code is RejectionCode.INVALID_FIELD
    assert MatchSettings(
        mode=MatchMode.COOP,
        level_id="duo-arena",
        content=CONTENT,
        tick_rate=60,
        max_players=2,
        cheats_enabled=True,
    ).cheats_enabled


def test_competitive_modes_are_named_and_versioned() -> None:
    assert {MatchMode.FREE_FOR_ALL, MatchMode.TEAM_BATTLE} == COMPETITIVE_MODES
    assert settings(MatchMode.FREE_FOR_ALL).competitive
    assert not settings().competitive
    assert settings().settings_version == MATCH_SETTINGS_VERSION
