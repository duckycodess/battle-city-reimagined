"""Render the online screens against a real server and write the tracked captures.

Run it from the repository root::

    uv run --locked python tests/multiplayer/screenshot_tool.py

pytest does not collect this module, and that is deliberate rather than incidental.
Everything else under ``tests/multiplayer`` runs without a display library, and
``tests/sim/test_purity.py`` asserts that ``pygame`` is absent from ``sys.modules`` as
its proxy for "importing the simulation does not pull in a display". ``tests/client``
goes to some trouble to put that precondition back after it runs; a second directory
importing pygame during collection would break it again, in a file that cannot fix it.
So the captures are produced by a tool that is run on purpose, and the images it writes
are tracked beside it.

Every frame here comes off the wire. The tool starts a real
:class:`~battle_city_server.SessionServer` over the loopback transport, drives two real
:class:`~battle_city_client.ClientShell` instances through the lobby, starts the match
and advances the authoritative tick. Nothing is injected and no state is assembled: what
the images show is what a player would see, which is the only reason a screenshot is
worth attaching to a pull request.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).parent))

import pygame  # noqa: E402
from battle_city_client import (  # noqa: E402
    ClientShell,
    ProceduralAssetLibrary,
    Renderer,
    theme,
)
from battle_city_client.intents import IDLE_INTENT, Action, PlayerIntent  # noqa: E402
from battle_city_client.online import OnlineConfig  # noqa: E402
from battle_city_content import Pack  # noqa: E402
from battle_city_protocol import (  # noqa: E402
    ClientChannel,
    MatchMode,
    TeamAssignment,
    client_channel,
)
from battle_city_server import SessionServer, loopback_pair  # noqa: E402
from battle_city_sim import DEFAULT_RULES, Direction  # noqa: E402
from multiplayer_helpers import (  # noqa: E402
    SESSION_ID,
    TICKETS,
    content_ref,
    make_server,
    write_pack,
)

CAPTURE_SCALE = 2
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
PLAY_TICKS = 24


class Peer:
    """One shell on one channel, pumped by hand, exactly as the frame loop does it."""

    def __init__(self, shell: ClientShell, channel: ClientChannel, task: asyncio.Task[None]):
        self.shell = shell
        self.channel = channel
        self.task = task

    async def pump(self, intent: PlayerIntent = IDLE_INTENT, rounds: int = 2) -> None:
        for _ in range(rounds):
            while True:
                try:
                    message = await asyncio.wait_for(self.channel.receive(), 0.02)
                except TimeoutError:
                    break
                if message is None:
                    self.shell.link_lost("SERVER CLOSED THE CONNECTION")
                    break
                self.shell.receive(message)
            if self.shell.drives_tank:
                self.shell.pump_online(intent)
            for outgoing in self.shell.take_outbox():
                await self.channel.send(outgoing)

    async def close(self) -> None:
        await self.channel.close()
        await self.task


def make_shell(pack: Pack, ticket: str, name: str) -> ClientShell:
    shell = ClientShell(
        catalog=(),
        online_config=OnlineConfig(
            endpoint="loopback:0",
            session_id=SESSION_ID,
            ticket=ticket,
            display_name=name,
            content=content_ref(pack),
        ),
    )
    shell.open_online()
    return shell


async def attach(server: SessionServer, shell: ClientShell) -> Peer:
    client_end, server_end = loopback_pair()
    task = asyncio.create_task(server.serve(server.channel_for(server_end)))
    await asyncio.sleep(0)
    peer = Peer(shell, client_channel(client_end), task)
    await peer.pump()
    return peer


def capture(shell: ClientShell, name: str, directory: Path) -> Path:
    """Render one shell state and write it at :data:`CAPTURE_SCALE`."""
    frame = pygame.Surface(theme.LOGICAL_SIZE)
    Renderer(ProceduralAssetLibrary(DEFAULT_RULES)).render(frame, shell)
    scaled = pygame.transform.scale_by(frame, CAPTURE_SCALE)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    pygame.image.save(scaled, str(path))
    return path


async def take_captures(pack: Pack, directory: Path) -> list[Path]:
    written: list[Path] = []
    server, match = make_server(pack)
    host = await attach(server, make_shell(pack, TICKETS[1], "ducky"))
    guest = await attach(server, make_shell(pack, TICKETS[2], "tj"))
    await host.pump()

    written.append(capture(host.shell, "01-lobby-waiting", directory))

    # A competitive mode, configured with teams, which the server will not start.
    session = host.shell.online
    assert session is not None
    configure = session.configure_message(
        mode=MatchMode.TEAM_BATTLE,
        teams=(TeamAssignment(slot=1, team=1), TeamAssignment(slot=2, team=2)),
    )
    assert configure is not None
    await host.channel.send(configure)
    await host.pump()
    await guest.pump()
    written.append(capture(host.shell, "02-lobby-competitive-blocked", directory))

    # Back to co-op, and both players agree.
    back = session.configure_message(mode=MatchMode.COOP)
    assert back is not None
    await host.channel.send(back)
    for peer in (host, guest):
        await peer.pump()
        peer.shell.handle(Action.ONLINE_READY)
        await peer.pump()
    await host.pump()
    await guest.pump()
    written.append(capture(host.shell, "03-lobby-ready", directory))

    host.shell.handle(Action.UI_CONFIRM)
    for _ in range(4):
        await host.pump()
        await guest.pump()

    for _ in range(PLAY_TICKS):
        await host.pump(PlayerIntent(direction=Direction.UP, fire=True))
        await guest.pump(PlayerIntent(direction=Direction.LEFT))
        await server.advance_tick()
    await host.pump()
    await guest.pump()
    assert match.game is not None
    written.append(capture(host.shell, "04-coop-play", directory))

    await server.close()
    await host.close()
    await guest.close()
    return written


def notes() -> str:
    sdl = ".".join(str(part) for part in pygame.version.SDL)
    window = theme.LOGICAL_SIZE[0] * CAPTURE_SCALE, theme.LOGICAL_SIZE[1] * CAPTURE_SCALE
    return f"""# Online screen captures

Generated by `tests/multiplayer/screenshot_tool.py`. Do not edit by hand. Refresh with:

```sh
uv run --locked python tests/multiplayer/screenshot_tool.py
```

| Capture | Screen |
| --- | --- |
| `01-lobby-waiting.png` | Lobby after two clients took their seats; nobody ready yet |
| `02-lobby-competitive-blocked.png` | Team battle selected: teams shown, start refused |
| `03-lobby-ready.png` | Co-op, both members agreed to the revision, server says startable |
| `04-coop-play.png` | The co-op run after {PLAY_TICKS} authoritative ticks |

## How they were taken

Every frame is rendered from a real client shell that was driven through a real
`SessionServer` over the loopback transport: the lobby messages, the handover, the
membership token and the snapshots are the ones a networked client would receive. No
state is injected and nothing is assembled by the tool.

`02-lobby-competitive-blocked.png` is the honest picture of competitive play in this
build. The mode is selectable, versioned and broadcast with its team assignments, and
the server refuses to start it with `mode_unsupported`, because the shared simulation
has one player faction, no player-versus-player damage and no competitive result. There
is deliberately no screenshot of a competitive match being played, because there is no
competitive match to play.

| | |
| --- | --- |
| Logical frame | {theme.LOGICAL_SIZE[0]}x{theme.LOGICAL_SIZE[1]} |
| Capture scale | {CAPTURE_SCALE}x (image is {window[0]}x{window[1]}) |
| SDL video driver | `{pygame.display.get_driver()}` (`SDL_VIDEODRIVER=dummy`) |
| pygame-ce | {pygame.version.ver}, SDL {sdl} |
| Python | {sys.version.split()[0]} on {sys.platform} |
| Stage | `duo-arena`, the two-spawn test pack written by `multiplayer_helpers` |
"""


def main() -> int:
    pygame.display.init()
    try:
        with tempfile.TemporaryDirectory() as scratch:
            pack = write_pack(Path(scratch))
            written = asyncio.run(take_captures(pack, SCREENSHOT_DIR))
        (SCREENSHOT_DIR / "README.md").write_text(notes(), encoding="utf-8")
    finally:
        pygame.display.quit()
        pygame.quit()
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
