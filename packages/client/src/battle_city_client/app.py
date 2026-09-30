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
from battle_city_sim import DEFAULT_RULES, Rules

from . import theme
from .assets import AssetLibrary, ProceduralAssetLibrary
from .display import Presenter, preferred_scale
from .intents import Action, HeldActions
from .keymap import edge_action_for, held_action_for
from .rendering import Renderer
from .session import DEFAULT_SEED
from .shell import ClientShell, Screen
from .stage_adapter import StageAdapterError, bundled_stage_catalog
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
        if screen is Screen.PLAYING:
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
            if self.shell.screen is not Screen.PLAYING:
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
        if not self.shell.consumes_ticks:
            self._stop_driving()
        return ticks

    def draw(self) -> None:
        """Render the current shell state and show it."""
        self.renderer.render(self.presenter.surface, self.shell)
        self.presenter.present()

    def step(self, elapsed_ms: int) -> None:
        """One whole frame: events, simulation, presentation."""
        self.pump_events()
        self.advance_frame(elapsed_ms)
        self.draw()

    def run(self) -> None:
        """Loop until the shell stops running."""
        self.clock.tick(self.frame_cap)
        while self.shell.running:
            self.step(self.clock.tick(self.frame_cap))


def build_app(
    *,
    seed: int = DEFAULT_SEED,
    rules: Rules = DEFAULT_RULES,
    scale: int | None = None,
    frame_cap: int = DEFAULT_FRAME_CAP,
    assets: AssetLibrary | None = None,
) -> ClientApp:
    """Load the bundled stages, open a window and wire the client together.

    A display must already be initialised. :func:`main` does that; a test does it with
    the dummy driver.
    """
    shell = ClientShell(catalog=bundled_stage_catalog(), seed=seed, rules=rules)
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
    return parser.parse_args(argv)


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
        pygame.display.init()
    except pygame.error as error:
        print(f"battle_city_client: no usable display: {error}")
        return 2
    try:
        try:
            app = build_app(seed=options.seed, scale=options.scale, frame_cap=options.frame_cap)
        except StageAdapterError as error:
            print(f"battle_city_client: {error}")
            return 1
        app.run()
    finally:
        pygame.display.quit()
        pygame.quit()
    return 0
