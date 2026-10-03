"""The client's real transport, against a real TCP server.

Every other test in this directory uses the loopback transport, which exercises the
framing, the decoder and the session but not the thread the client actually runs its
socket on. This one does: a :class:`~battle_city_client.netlink.TcpLink` opens a real
connection to a real listener on an ephemeral port, and the shell is pumped exactly as
the frame loop pumps it. It is the one place the threading is proved rather than assumed.

Everything is bounded. The link is polled in a loop with a deadline, so a failure here
is a failed assertion rather than a hung suite.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from battle_city_client.netlink import TcpLink, parse_endpoint
from battle_city_client.online import OnlineConfig, OnlinePhase
from battle_city_client.shell import ClientShell, Screen
from battle_city_content import Pack
from battle_city_server import serve_tcp
from multiplayer_helpers import SESSION_ID, TICKETS, content_ref, make_server, write_pack

PUMP_TIMEOUT = 5.0
PUMP_INTERVAL = 0.01


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


def make_shell(pack: Pack, ticket: str, name: str, port: int) -> ClientShell:
    shell = ClientShell(
        catalog=(),
        online_config=OnlineConfig(
            endpoint=f"127.0.0.1:{port}",
            session_id=SESSION_ID,
            ticket=ticket,
            display_name=name,
            content=content_ref(pack),
        ),
    )
    assert shell.open_online()
    return shell


async def pump_until(
    shell: ClientShell, link: TcpLink, done: Callable[[], bool], timeout: float = PUMP_TIMEOUT
) -> None:
    """Drive the loop's network step until ``done`` or the deadline, whichever first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in link.poll():
            shell.receive(message)
        if not link.open:
            shell.link_lost(link.failure or "")
        for outgoing in shell.take_outbox():
            link.send(outgoing)
        if done():
            return
        await asyncio.sleep(PUMP_INTERVAL)
    raise AssertionError(f"condition not reached in {timeout}s; link failure={link.failure}")


def test_a_tcp_client_reaches_the_lobby_and_leaves_cleanly(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        shell = make_shell(pack, TICKETS[1], "ducky", port)
        link = TcpLink("127.0.0.1", port)
        try:
            await pump_until(shell, link, lambda: shell.online_phase is OnlinePhase.LOBBY)
            assert shell.screen is Screen.ONLINE_LOBBY
            session = shell.online
            assert session is not None
            assert session.slot == 1
            assert session.host
            await pump_until(shell, link, lambda: session.lobby is not None)
            lobby = session.lobby
            assert lobby is not None
            assert [member.display_name for member in lobby.members] == ["ducky"]
            assert match.lobby.occupied_slots() == (1,)
        finally:
            link.close()
            await server.close()
            listener.close()
            await listener.wait_closed()
        assert not link.open

    run(scenario)


def test_a_refused_connection_ends_the_session_without_an_outcome(tmp_path: Path) -> None:
    """Nothing is listening, so the link fails and says so. No run is invented."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        # Bind and immediately release a port, so the address is almost certainly free.
        listener = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        listener.close()
        await listener.wait_closed()

        shell = make_shell(pack, TICKETS[1], "ducky", port)
        link = TcpLink("127.0.0.1", port, connect_timeout=1.0)
        try:
            await pump_until(shell, link, lambda: shell.online_phase is OnlinePhase.ENDED)
        finally:
            link.close()
        session = shell.online
        assert session is not None
        assert session.outcome is None
        assert session.notice

    run(scenario)


@pytest.mark.parametrize("endpoint", ["127.0.0.1:1234", "localhost:65535", "[::1]:9000"])
def test_an_endpoint_is_parsed_once_for_every_caller(endpoint: str) -> None:
    host, port = parse_endpoint(endpoint)
    assert host
    assert 1 <= port <= 65535


@pytest.mark.parametrize("endpoint", ["127.0.0.1", ":80", "host:0", "host:70000", "host:web"])
def test_a_malformed_endpoint_is_refused_by_name(endpoint: str) -> None:
    with pytest.raises(ValueError):
        parse_endpoint(endpoint)
