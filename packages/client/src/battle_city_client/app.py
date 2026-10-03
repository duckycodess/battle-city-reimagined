"""The pygame-ce loop: window, events, frame pacing, and nothing else.

This is the only module that touches a device. It owns the window through
:class:`~battle_city_client.display.Presenter`, turns window events into
:class:`~battle_city_client.intents.Action` values through
:mod:`battle_city_client.keymap`, converts elapsed milliseconds into ticks through
:class:`~battle_city_client.timing.FixedTickAccumulator`, and hands both to the shell.
Every decision about what those inputs *mean* was made before this file: the loop is a
pump.

Frame cadence does not decide simulation cadence. The loop measures how long the last
frame took and asks the accumulator how many whole ticks that buys; a frame that took
twice as long releases twice as many ticks, and a frame that took none releases none. A
display running at 144Hz and one running at 30Hz play the same run at the same speed, and
:meth:`ClientApp.advance_frame` takes the elapsed time as an argument so a test can prove
that without a display.

Audio is never initialised. ``pygame.init()`` brings up the mixer, which needs a sound
device that a container, a CI runner or a headless desktop may not have; the client calls
:func:`pygame.display.init` on its own and this build plays no sound, so there is nothing
to lose by not asking. The display subsystem itself honours ``SDL_VIDEODRIVER``, so
``SDL_VIDEODRIVER=dummy`` runs the whole loop with no window at all.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import pygame
from battle_city_protocol import ContentRef, MessageError
from battle_city_sim import DEFAULT_RULES, Rules

from . import theme
from .assets import AssetLibrary, ProceduralAssetLibrary
from .display import Presenter, preferred_scale
from .intents import Action, HeldActions
from .keymap import edge_action_for, held_action_for
from .netlink import NetworkLink, open_tcp_link, parse_endpoint
from .online import OnlineConfig
from .rendering import Renderer
from .session import DEFAULT_SEED
from .shell import ClientShell
from .stage_adapter import StageAdapterError, bundled_content_ref, bundled_stage_catalog
from .timing import NOMINAL_TICK_RATE, FixedTickAccumulator

DEFAULT_FRAME_CAP: int = 120
"""Frames per second the loop aims for.

A cap above the tick rate keeps input latency low without spinning a core; the
accumulator makes the exact number irrelevant to how the run plays.
"""

MIN_FRAME_CAP: int = 1
MAX_FRAME_CAP: int = 1000
"""Bounds for ``--frame-cap``. Zero means "never wait" to pygame, which is a busy spin."""

WINDOW_CAPTION: str = "Battle City Reimagined"

DEFAULT_DISPLAY_NAME: str = "player"
"""Roster name used when the launch did not supply one. Bounded by the protocol."""


class ClientApp:
    """The loop, assembled from parts that are each usable without it."""

    def __init__(
        self,
        shell: ClientShell,
        presenter: Presenter,
        renderer: Renderer,
        *,
        accumulator: FixedTickAccumulator | None = None,
        frame_cap: int = DEFAULT_FRAME_CAP,
    ) -> None:
        self.shell = shell
        self.presenter = presenter
        self.renderer = renderer
        self.accumulator = accumulator or FixedTickAccumulator(tick_rate=NOMINAL_TICK_RATE)
        self.frame_cap = frame_cap
        self.held = HeldActions()
        self.clock = pygame.time.Clock()
        self.link: NetworkLink | None = None

    # -- events ----------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        """Apply one window event."""
        match event.type:
            case pygame.QUIT:
                self.shell.quit()
            case pygame.VIDEORESIZE:
                self.presenter.resize((event.w, event.h))
            case pygame.WINDOWFOCUSLOST:
                self._set_focused(False)
            case pygame.WINDOWFOCUSGAINED:
                self._set_focused(True)
            case pygame.KEYDOWN:
                self._handle_key_down(event.key)
            case pygame.KEYUP:
                held = held_action_for(event.key)
                if held is not None:
                    self.held.release(held)
            case _:
                return

    def pump_events(self) -> None:
        """Drain the event queue into the shell."""
        for event in pygame.event.get():
            self.handle_event(event)

    def _handle_key_down(self, key: int) -> None:
        """Route one key press, recording it as held only while a run is being driven.

        A key can be a continuous control on one screen and a menu control on another:
        ``SPACE`` fires during a run and confirms a menu choice everywhere else. Banking
        it as held regardless of the screen means the press that started the stage, or
        resumed it, or retried it, is still down on the run's first tick, and the tank
        opens fire on a keystroke the player aimed at a menu. Held state is therefore
        only collected while the shell is actually sampling it, and is dropped whenever
        the shell stops -- the same reasoning as losing window focus, where input keeps
        happening but nothing is watching it.
        """
        screen = self.shell.screen
        if self.shell.drives_tank:
            held = held_action_for(key)
            if held is not None:
                self.held.press(held)
        edge = edge_action_for(key, screen)
        if edge is None:
            return
        if edge is Action.SCALE_UP:
            self.presenter.step_scale(1)
        elif edge is Action.SCALE_DOWN:
            self.presenter.step_scale(-1)
        else:
            self.shell.handle(edge)
            if not self.shell.drives_tank:
                self.held.clear()

    def _set_focused(self, focused: bool) -> None:
        """Track focus, and forget held keys when the window stops receiving releases."""
        if not focused:
            self._stop_driving()
        self.shell.set_focused(focused)

    def _stop_driving(self) -> None:
        """Drop banked wall time and held keys together.

        The two belong to the same moment. Once the shell stops consuming ticks nothing
        is sampling the keyboard, so both the elapsed time and the keys that were down
        describe a stretch the run did not live through. Keeping either one means the
        next run inherits it: banked time as a burst of catch-up ticks, a held key as an
        action the player never pressed for that run.
        """
        self.accumulator.reset()
        self.held.clear()

    # -- frame -----------------------------------------------------------------

    def advance_frame(self, elapsed_ms: int) -> int:
        """Consume ``elapsed_ms`` of wall time and advance the run. Returns ticks run.

        The shell can stop consuming ticks inside :meth:`ClientShell.advance` rather than
        under a keystroke: a run that reaches a terminal outcome leaves for the terminal
        screen on its own. That transition has no key press behind it to tidy up after
        it, so it is tidied up here -- otherwise a fire key held as the run ended would
        still be held when the player chose *retry*, and the next run would open with a
        shot nobody asked for.
        """
        ticks = 0
        if self.shell.consumes_ticks:
            ticks = self.accumulator.advance(elapsed_ms)
            self.shell.advance(ticks, self.held.intent())
        if not self.shell.drives_tank:
            self._stop_driving()
        return ticks

    # -- network ---------------------------------------------------------------

    def pump_network(self) -> None:
        """Open, drain and feed the link, once per frame.

        Everything about *what* the messages mean happened before this method: the
        shell holds the session, the session holds the rules about what it may say, and
        this is the pump that moves bytes between them. It never advances a simulation
        and never fabricates a message the session did not offer.
        """
        self._reconcile_link()
        link = self.link
        if link is None:
            return
        for message in link.poll():
            self.shell.receive(message)
        if not link.open:
            self.shell.link_lost(link.failure or "")
        if self.shell.drives_tank:
            self.shell.pump_online(self.held.intent())
        for outgoing in self.shell.take_outbox():
            link.send(outgoing)

    def _reconcile_link(self) -> None:
        """Hold a link exactly while the shell is on an online screen.

        The shell says which screen it is on; opening and closing a socket is this
        layer's job. A link is never reopened after it fails: there is no reconnect in
        this release, so a second dial would be a new connection with no claim on the
        slot the first one held.
        """
        session = self.shell.online
        if (
            self.shell.wants_link
            and self.link is None
            and self.shell.online_config is not None
            and session is not None
            and session.live
        ):
            try:
                self.link = open_tcp_link(self.shell.online_config.endpoint)
            except (OSError, ValueError) as error:
                # A session whose link could not be opened is over. Checking ``live``
                # above is what stops the next frame trying again, and the one after
                # that: a dial that failed for a reason the frame loop cannot change
                # will fail the same way sixty times a second.
                self.shell.link_lost(type(error).__name__.upper())
            return
        if not self.shell.wants_link and self.link is not None:
            self.shell.take_outbox()
            self.link.close()
            self.link = None

    def draw(self) -> None:
        """Render the current shell state and show it."""
        self.renderer.render(self.presenter.surface, self.shell)
        self.presenter.present()

    def step(self, elapsed_ms: int) -> None:
        """One whole frame: events, network, simulation, presentation."""
        self.pump_events()
        self.pump_network()
        self.advance_frame(elapsed_ms)
        self.draw()

    def run(self) -> None:
        """Loop until the shell stops running."""
        self.clock.tick(self.frame_cap)
        try:
            while self.shell.running:
                self.step(self.clock.tick(self.frame_cap))
        finally:
            self.close()

    def close(self) -> None:
        """Release the network link, if one is open. Idempotent."""
        if self.link is not None:
            self.link.close()
            self.link = None


def build_app(
    *,
    seed: int = DEFAULT_SEED,
    rules: Rules = DEFAULT_RULES,
    scale: int | None = None,
    frame_cap: int = DEFAULT_FRAME_CAP,
    assets: AssetLibrary | None = None,
    online: OnlineConfig | None = None,
) -> ClientApp:
    """Load the bundled stages, open a window and wire the client together.

    A display must already be initialised. :func:`main` does that; a test does it with
    the dummy driver.

    ``online`` is the lobby this client can reach, when it was given one. Without it the
    online menu entry says what is missing rather than disappearing, because a build
    that can play online and a launch that was not told where are two different things.
    """
    shell = ClientShell(
        catalog=bundled_stage_catalog(), seed=seed, rules=rules, online_config=online
    )
    presenter = Presenter(
        scale=preferred_scale() if scale is None else scale, caption=WINDOW_CAPTION
    )
    renderer = Renderer(assets or ProceduralAssetLibrary(rules))
    return ClientApp(shell, presenter, renderer, frame_cap=frame_cap)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the launch options."""
    parser = argparse.ArgumentParser(
        prog="battle_city_client",
        description="Play a bundled Battle City Reimagined stage.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="simulation seed for the run (default: %(default)s)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=None,
        choices=range(theme.MIN_SCALE, theme.MAX_SCALE + 1),
        help="whole-number window scale; the default is chosen from the desktop size",
    )
    parser.add_argument(
        "--frame-cap",
        type=_frame_cap,
        default=DEFAULT_FRAME_CAP,
        help=(
            f"frames per second the loop aims for, {MIN_FRAME_CAP}-{MAX_FRAME_CAP} "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--server",
        default=None,
        metavar="HOST:PORT",
        help="lobby to join when ONLINE is chosen; without it ONLINE says what is missing",
    )
    parser.add_argument(
        "--session",
        default=None,
        metavar="ID",
        help="identifier of the session to join on that server",
    )
    parser.add_argument(
        "--ticket",
        default=None,
        metavar="TICKET",
        help="the lobby ticket issued for this player",
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_DISPLAY_NAME,
        metavar="NAME",
        help="roster name other players see (default: %(default)s)",
    )
    parser.add_argument(
        "--content",
        default=None,
        metavar="PACK@VERSION/LEVEL#SCHEMA",
        help="content this client claims to be running; the bundled pack by default",
    )
    return parser.parse_args(argv)


def online_config(options: argparse.Namespace) -> OnlineConfig | None:
    """Build the online details from the launch options, or ``None`` when absent.

    All three of the address, the session and the ticket are required together: two of
    them describe a lobby this client cannot prove it belongs to, which is a launch
    mistake worth naming rather than a half-configured online mode.
    """
    supplied = (options.server, options.session, options.ticket)
    if not any(supplied):
        return None
    if not all(supplied):
        raise ValueError("--server, --session and --ticket are required together")
    parse_endpoint(options.server)
    content = (
        bundled_content_ref() if options.content is None else parse_content_ref(options.content)
    )
    return OnlineConfig(
        endpoint=options.server,
        session_id=options.session,
        ticket=options.ticket,
        display_name=options.name,
        content=content,
    )


def parse_content_ref(value: str) -> ContentRef:
    """Parse ``pack@version/level#schema``, the spelling the server's logs use.

    One spelling for both sides means a mismatch can be read straight off a log line
    and pasted back in, instead of being reassembled from four separate flags.
    """
    pack, separator, rest = value.partition("@")
    version, slash, remainder = rest.partition("/")
    level, hash_mark, schema = remainder.partition("#")
    if not (separator and slash and hash_mark):
        raise ValueError(f"expected PACK@VERSION/LEVEL#SCHEMA, found {value!r}")
    try:
        schema_version = int(schema)
    except ValueError:
        raise ValueError(f"{schema!r} is not a content schema version") from None
    return ContentRef(
        pack_id=pack,
        pack_version=version,
        level_id=level,
        content_schema_version=schema_version,
    )


def _frame_cap(value: str) -> int:
    """Parse and bound ``--frame-cap``.

    ``pygame.time.Clock.tick(0)`` means "do not wait", so an unchecked zero turns the
    loop into a busy spin that renders thousands of identical frames a second. The run
    itself is unaffected -- the accumulator decides how many ticks a frame buys -- which
    is exactly why the mistake would be invisible rather than obvious.
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if not MIN_FRAME_CAP <= parsed <= MAX_FRAME_CAP:
        raise argparse.ArgumentTypeError(
            f"frame cap must be between {MIN_FRAME_CAP} and {MAX_FRAME_CAP}, found {parsed}"
        )
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the client. Returns a process exit status.

    Everything after the display comes up is wound down in one ``finally``, including the
    paths that never reach the loop. A malformed content pack used to return early with
    SDL still initialised, which leaves a window on screen with nothing driving it and
    leaves the subsystem up for whatever runs next in the same interpreter.
    """
    options = parse_args(argv)
    try:
        online = online_config(options)
    except (ValueError, MessageError) as error:
        print(f"battle_city_client: {error}")
        return 1
    try:
        pygame.display.init()
    except pygame.error as error:
        print(f"battle_city_client: no usable display: {error}")
        return 2
    try:
        try:
            app = build_app(
                seed=options.seed,
                scale=options.scale,
                frame_cap=options.frame_cap,
                online=online,
            )
        except StageAdapterError as error:
            print(f"battle_city_client: {error}")
            return 1
        app.run()
    finally:
        pygame.display.quit()
        pygame.quit()
    return 0
