"""Session logs: every field the architecture specification asks for, and no secret.

A log that cannot answer "which session, which tick, which build, which content, why"
is not much use during an incident, and one that answers them by printing a membership
token is worse than useless. Both halves are asserted here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from battle_city_protocol import PROTOCOL_VERSION, RejectionCode, encode_frame, encode_message
from battle_city_server import FIELD_ORDER, LOGGER_NAME, SessionLimits, SessionServer, rules_digest
from server_helpers import (
    SESSION_ID,
    TOKENS,
    BlockedStream,
    connect,
    join_request,
    joined,
    make_config,
)

REQUIRED_FIELDS = ("event", "session_id", "tick", "protocol_version", "content", "rules_digest")


def field(record: logging.LogRecord, name: str) -> Any:
    return getattr(record, name, None)


def events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if field(record, "event") == name]


def test_a_join_is_logged_with_session_tick_version_content_and_rules(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        await server.advance_tick()
        await client.close()

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    joins = events(caplog, "join_accepted")
    assert len(joins) == 1
    record = joins[0]
    for name in REQUIRED_FIELDS:
        assert field(record, name) is not None, name
    assert field(record, "session_id") == SESSION_ID
    assert field(record, "protocol_version") == PROTOCOL_VERSION
    assert field(record, "content") == "classic@1.0.0/test-stage#1"
    assert field(record, "rules_digest") == rules_digest()
    assert field(record, "slot") == 1
    assert field(record, "tick") == 0


def test_the_rendered_line_carries_the_same_fields(caplog: pytest.LogCaptureFixture) -> None:
    """A text handler must stay greppable, not just a structured one."""

    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        await client.close()

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    line = events(caplog, "join_accepted")[0].getMessage()
    assert line.startswith("event=join_accepted ")
    for name in REQUIRED_FIELDS:
        assert f"{name}=" in line

    rendered = [pair.split("=", 1)[0] for pair in line.split(" ")]
    assert rendered == [name for name in FIELD_ORDER if name in rendered]


def test_a_refusal_is_logged_once_with_its_stable_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await connect(server)
        await client.send(join_request(1, token="wrong-token-aaaaaaaa"))
        await client.receive()
        await client.close()

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    refusals = events(caplog, "message_refused")
    assert len(refusals) == 1
    assert field(refusals[0], "reason") == RejectionCode.INVALID_TOKEN.value
    assert refusals[0].levelno == logging.WARNING


def test_a_malformed_frame_is_logged_once_not_twice(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        await client.stream.write(encode_frame(b"not json"))
        await client.receive()
        await asyncio.wait_for(client.task, 1.0)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    refusals = events(caplog, "message_refused")
    assert len(refusals) == 1
    assert field(refusals[0], "reason") == RejectionCode.MALFORMED_FRAME.value


def test_the_connection_lifecycle_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        await client.close()

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    assert len(events(caplog, "connection_opened")) == 1
    dropped = events(caplog, "connection_dropped")
    assert len(dropped) == 1
    assert field(dropped[0], "slot") == 1
    assert field(dropped[0], "peer")


def test_a_refused_connection_is_logged_with_its_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=SessionLimits(max_connections=1)))
        first = await connect(server)
        second = await connect(server)
        await second.receive()
        await asyncio.wait_for(second.task, 1.0)
        await first.close()

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    refused = events(caplog, "connection_refused")
    assert len(refused) == 1
    assert field(refused[0], "reason") == RejectionCode.TOO_MANY_CONNECTIONS.value


def test_an_overflowing_client_is_logged_with_its_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        limits = SessionLimits(max_outbound_messages=2)
        server = SessionServer(make_config(limits=limits), flush_timeout=0.05)
        stream = BlockedStream(encode_frame(encode_message(join_request(1))))
        task = asyncio.create_task(server.serve(server.channel_for(stream)))
        await asyncio.sleep(0)
        for _ in range(8):
            await server.advance_tick()
        stream.released.set()
        await asyncio.wait_for(task, 2.0)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    closed = [
        record
        for record in events(caplog, "connection_closed")
        if field(record, "reason") == RejectionCode.QUEUE_OVERFLOW.value
    ]
    assert len(closed) == 1
    assert closed[0].levelno == logging.WARNING
    assert field(closed[0], "tick") is not None


def test_the_session_end_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        await server.close(RejectionCode.SERVER_SHUTDOWN, "going down")
        await asyncio.wait_for(client.task, 1.0)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    ended = events(caplog, "session_closed")
    assert len(ended) == 1
    assert field(ended[0], "reason") == RejectionCode.SERVER_SHUTDOWN.value
    assert field(ended[0], "detail") == "going down"


def test_no_record_anywhere_carries_a_membership_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one assertion that has to hold across every path, so it drives every path."""

    async def scenario() -> None:
        server = SessionServer(make_config(limits=SessionLimits(max_join_attempts=9)))
        good = await joined(server, 1)
        await server.advance_tick()

        guesser = await connect(server)
        await guesser.send(join_request(2, token=TOKENS[1]))
        await guesser.receive()
        await guesser.send(join_request(2, token="almost-right-bbbbbb"))
        await guesser.receive()
        await guesser.close()

        await server.close(RejectionCode.SERVER_SHUTDOWN, "done")
        await asyncio.wait_for(good.task, 1.0)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        asyncio.run(scenario())

    assert caplog.records
    secrets = (*TOKENS.values(), "almost-right-bbbbbb")
    for record in caplog.records:
        haystack = " ".join(
            [record.getMessage(), *(str(value) for value in record.__dict__.values())]
        )
        for secret in secrets:
            assert secret not in haystack, f"{field(record, 'event')} leaked a token"
