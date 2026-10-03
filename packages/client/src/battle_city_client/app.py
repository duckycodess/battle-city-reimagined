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

It is also where the local profile is opened. :class:`ClientShell` keeps settings and
campaign progress in memory by default and writes nothing; a real launch is the one place
that hands it a :class:`~battle_city_client.persistence.ProfileStore`, so a window and a
save file come up together or not at all. Opening the profile cannot fail a launch: an
unreadable file leaves the client on its defaults with a notice on screen. See
:mod:`battle_city_client.persistence`.

Audio is never initialised. ``pygame.init()`` brings up the mixer, which needs a sound
device that a container, a CI runner or a headless desktop may not have; the client calls
:func:`pygame.display.init` on its own and this build plays no sound, so there is nothing
to lose by not asking. The display subsystem itself honours ``SDL_VIDEODRIVER``, so
``SDL_VIDEODRIVER=dummy`` runs the whole loop with no window at all. The options screen
has independent effect and music controls and says plainly that they are inactive: the
preference model is real, the engine is not, and bringing a mixer up to prove it would
be the one thing that could cost a headless machine its launch.

The joystick subsystem *is* brought up, behind the same kind of guard: a machine without
one gets a hub with no pads in it and the keyboard client it always had. Pads are
followed while the game runs -- plugged in, unplugged, and a window that stops being
told what they are doing -- and in each of the last two cases exactly that device's held
input is neutralised. See :mod:`battle_city_client.gamepad`.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import pygame
from battle_city_protocol import ContentRef, MessageError
from battle_city_sim import DEFAULT_RULES, Rules

from . import theme
from .accessibility import (
    REPEATABLE_ACTIONS,
    AccessibilityPreferences,
    GamepadControl,
    RepeatTimer,
    edge_control_action,
    held_control_action,
)
from .assets import AssetLibrary, ProceduralAssetLibrary
from .display import Presenter, preferred_scale
from .gamepad import ControlEvent, GamepadHub, close_gamepads, open_gamepads
from .intents import Action, HeldActions
from .keymap import DEFAULT_BINDINGS, edge_action_for, held_action_for
from .netlink import NetworkLink, open_tcp_link, parse_endpoint
from .online import OnlineConfig
from .persistence import (
    DEFAULT_DISPLAY_NAME,
    DEFAULT_FRAME_CAP,
    MAX_FRAME_CAP,
    MIN_FRAME_CAP,
    LocalProfile,
    LocalSettings,
    ProfileStore,
)
from .rendering import Renderer
from .session import DEFAULT_SEED
from .shell import ClientShell
from .stage_adapter import StageAdapterError, bundled_content_ref, bundled_stage_catalog
from .timing import NOMINAL_TICK_RATE, FixedTickAccumulator

WINDOW_CAPTION: str = "Battle City Reimagined"

# ``DEFAULT_FRAME_CAP``, ``MIN_FRAME_CAP``, ``MAX_FRAME_CAP`` and ``DEFAULT_DISPLAY_NAME``
# are imported above and re-exported here, where they have always been published. They are
# defined in :mod:`battle_city_client.persistence.settings` because a saved settings file
# is validated against exactly these bounds, and a launch flag that disagreed with the file
# it is saved into would be two answers to one question.
__all__ = [
    "DEFAULT_DISPLAY_NAME",
    "DEFAULT_FRAME_CAP",
    "MAX_FRAME_CAP",
    "MIN_FRAME_CAP",
    "WINDOW_CAPTION",
    "ClientApp",
    "build_app",
    "main",
    "online_config",
    "parse_args",
    "parse_content_ref",
]


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
        gamepads: GamepadHub | None = None,
    ) -> None:
        self.shell = shell
        self.presenter = presenter
        self.renderer = renderer
        self.accumulator = accumulator or FixedTickAccumulator(tick_rate=NOMINAL_TICK_RATE)
        self.frame_cap = frame_cap
        self.held = HeldActions()
        self.clock = pygame.time.Clock()
        self.link: NetworkLink | None = None
        self.gamepads = gamepads if gamepads is not None else GamepadHub()
        """Open pads. An app built without any gets an empty hub, not a missing one."""

        self.repeat = RepeatTimer(shell.accessibility.repeat)
        """Menu-cursor repeat for a held pad direction. Never reaches a run; see below."""

        self._renderers: dict[tuple[theme.ContrastMode, bool], Renderer] = {
            (shell.accessibility.contrast, shell.accessibility.reduced_motion): renderer
        }
        """One renderer per presentation preference, kept so switching back is warm.

        Colour is baked into the asset cache, so a contrast change is a different library
        and a different renderer rather than a tint. Building them lazily and keeping
        them means the switch costs one redraw of the stand-in art the first time and
        nothing after that.
        """

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
                held = held_action_for(event.key, self.shell.accessibility.bindings)
                if held is not None:
                    self.held.release(held)
            case pygame.JOYDEVICEADDED:
                self.gamepads.attach(int(event.device_index))
            case pygame.JOYDEVICEREMOVED:
                self._device_lost(int(event.instance_id))
            case (
                pygame.JOYBUTTONDOWN
                | pygame.JOYBUTTONUP
                | pygame.JOYAXISMOTION
                | pygame.JOYHATMOTION
            ):
                control = self.gamepads.handle_event(
                    event, self.shell.accessibility.dead_zone_percent
                )
                if control is not None:
                    self._handle_control(control)
            case _:
                return

    def _device_lost(self, instance: int) -> None:
        """A pad was unplugged: drop exactly what it was holding, and nothing else.

        SDL delivers no release for a stick that was pushed when the pad was pulled out,
        so its contribution would stay down for the rest of the run. Clearing the whole
        held set instead would release a key the player is still holding on the keyboard,
        which is why :meth:`~battle_city_client.intents.HeldActions.clear_device` exists.
        The menu repeat goes with it: it was being driven by a direction that is gone.
        """
        self.gamepads.detach(instance)
        self.held.clear_device(instance)
        self.repeat.clear()

    def _handle_control(self, event: ControlEvent) -> None:
        """Route one pad control, by the same rules a key is routed by.

        Held state is collected only while the shell is sampling it, repeats are started
        only where a cursor is, and an armed binding row takes the control instead of
        acting on it -- each for the reason the keyboard path gives.
        """
        bindings = self.shell.accessibility.bindings
        if self.shell.capturing:
            if event.pressed:
                self._capture_control(event.control)
            return
        held = held_control_action(event.control, bindings)
        edge = edge_control_action(event.control, bindings)
        if not event.pressed:
            if held is not None:
                self.held.release(held, event.device)
            if edge is not None:
                self.repeat.release(edge)
            return
        if held is not None and self.shell.drives_tank:
            self.held.press(held, event.device)
        if edge is None:
            return
        self._apply_edge(edge)
        if edge in REPEATABLE_ACTIONS and not self.shell.drives_tank:
            self.repeat.press(edge)

    def _capture_control(self, control: object) -> None:
        """Offer one pad control to the armed binding row, or cancel on the pad's back.

        The pad keeps a way out of a capture for the same reason the keyboard does: a
        player remapping with a pad must be able to abandon a row without reaching for a
        keyboard they may not be using.
        """
        if not isinstance(control, GamepadControl):
            return
        if edge_control_action(control) is Action.UI_CANCEL:
            self.shell.cancel_capture()
            return
        self.shell.capture_control(control)

    def pump_events(self) -> None:
        """Drain the event queue into the shell."""
        for event in pygame.event.get():
            self.handle_event(event)

    def _apply_edge(self, edge: Action) -> None:
        """Act on one edge action, whichever device produced it.

        Window scaling is the loop's own and never reaches the shell. Everything else
        goes to the shell, and the held set is dropped whenever the shell stops sampling
        it -- the transition out of a run has no release event behind it.
        """
        if edge is Action.SCALE_UP:
            self._step_scale(1)
            return
        if edge is Action.SCALE_DOWN:
            self._step_scale(-1)
            return
        before = self.shell.screen
        self.shell.handle(edge)
        if not self.shell.drives_tank:
            self.held.clear()
        if self.shell.screen is not before:
            # A direction held through a screen change must not keep stepping a cursor
            # on the screen that replaced it.
            self.repeat.clear()

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
        bindings = self.shell.accessibility.bindings
        screen = self.shell.screen
        if self.shell.capturing:
            # An armed binding row takes the keystroke whole: translating it first would
            # let the key that is about to become *fire* fire on its way in, and would
            # let the key that is about to become *confirm* leave the screen.
            if edge_action_for(key, screen, bindings) is Action.UI_CANCEL:
                self.shell.cancel_capture()
            else:
                self.shell.capture_key(key)
            return
        if self.shell.drives_tank:
            held = held_action_for(key, bindings)
            if held is not None:
                self.held.press(held)
        edge = edge_action_for(key, screen, bindings)
        if edge is None:
            return
        self._apply_edge(edge)

    def _step_scale(self, delta: int) -> None:
        """Resize the window, and remember the size the player settled on.

        The scale is the one setting this build lets a player change while it is running,
        so it is the one that is written back. The profile ignores a value that did not
        move -- pressing ``+`` at the largest scale changes nothing and writes nothing --
        and a profile with no file behind it, which is every profile but a real launch's,
        writes nothing at all.
        """
        self.presenter.step_scale(delta)
        self.shell.profile.remember_scale(self.presenter.requested_scale)

    def _set_focused(self, focused: bool) -> None:
        """Track focus, and forget held keys when the window stops receiving releases."""
        if not focused:
            self._stop_driving()
            self._release_devices()
        self.shell.set_focused(focused)

    def _release_devices(self) -> None:
        """Neutralise every device's held input, and forget what each was last reading.

        A window that is not focused is told nothing: no key release, and no stick
        returning to centre. Both would otherwise survive into the next time the window
        is looked at, so the held set is dropped, the repeat stops, and each pad's
        remembered axis and hat positions go with them -- the next reading after focus
        returns is then judged as new rather than against one taken minutes ago.
        """
        self.held.clear()
        self.repeat.clear()
        for device in self.gamepads.devices:
            self.gamepads.forget_readings(device)

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

    def pump_repeat(self, elapsed_ms: int) -> tuple[Action, ...]:
        """Release the menu repeats that fell due, and apply them. Returns what fired.

        Nothing is released while a run is being driven, and the timer is emptied there
        as well, so a stick held into a stage cannot keep producing cursor actions behind
        the run. Repeats are cursor actions by construction -- see
        :data:`~battle_city_client.accessibility.REPEATABLE_ACTIONS` -- so none of them
        can reach a tick: they are never put into the held set, and the intent a tick is
        built from reads nothing but the held set.
        """
        if self.shell.drives_tank or self.shell.capturing:
            self.repeat.clear()
            return ()
        due = self.repeat.advance(elapsed_ms)
        for action in due:
            self._apply_edge(action)
        return due

    def sync_preferences(self) -> None:
        """Adopt whatever the options screen changed since the last frame.

        Three things can move: the palette, which is baked into the asset cache and so
        means a different renderer; the reduced-motion preference, which is carried by
        the renderer for effects that do not exist yet; and the repeat timings, which the
        timer reads. Comparing rather than listening keeps the shell free of a callback
        into the loop, and all three are cheap value comparisons.
        """
        preferences = self.shell.accessibility
        self.repeat.options = preferences.repeat
        key = (preferences.contrast, preferences.reduced_motion)
        renderer = self._renderers.get(key)
        if renderer is None:
            renderer = self.renderer.with_palette(
                theme.palette_for(preferences.contrast)
            ).with_reduced_motion(preferences.reduced_motion)
            self._renderers[key] = renderer
        self.renderer = renderer

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
        self.sync_preferences()
        self.renderer.render(self.presenter.surface, self.shell)
        self.presenter.present()

    def step(self, elapsed_ms: int) -> None:
        """One whole frame: events, repeats, network, simulation, presentation."""
        self.pump_events()
        self.pump_repeat(elapsed_ms)
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
        """Release the network link and the pads, if either is open. Idempotent."""
        if self.link is not None:
            self.link.close()
            self.link = None
        close_gamepads(self.gamepads)


def build_app(
    *,
    seed: int = DEFAULT_SEED,
    rules: Rules = DEFAULT_RULES,
    scale: int | None = None,
    frame_cap: int = DEFAULT_FRAME_CAP,
    assets: AssetLibrary | None = None,
    online: OnlineConfig | None = None,
    profile: LocalProfile | None = None,
    accessibility: AccessibilityPreferences | None = None,
    gamepads: GamepadHub | None = None,
) -> ClientApp:
    """Load the bundled stages, open a window and wire the client together.

    A display must already be initialised. :func:`main` does that; a test does it with
    the dummy driver.

    ``online`` is the lobby this client can reach, when it was given one. Without it the
    online menu entry says what is missing rather than disappearing, because a build
    that can play online and a launch that was not told where are two different things.

    ``profile`` defaults to one with no file behind it, so building an app touches no
    disk. :func:`main` is the only caller that attaches a store, because only a real
    launch has a player whose settings and progress are worth keeping; a test, an
    embedding or a headless run gets the same client with its persistence in memory.

    ``accessibility`` defaults to the shipped preferences carrying the real key tables,
    which is what lets the options screen say what a control is bound to and refuse a
    key that is already taken. The preferences themselves are not read from anywhere:
    they last for this launch and are never written. ``gamepads`` defaults to whatever
    is plugged in, opened through a guard that cannot fail a launch -- a machine with no
    joystick subsystem gets an empty hub and the keyboard client it always had.
    """
    preferences = (
        accessibility
        if accessibility is not None
        else AccessibilityPreferences(bindings=DEFAULT_BINDINGS)
    )
    shell = ClientShell(
        catalog=bundled_stage_catalog(),
        seed=seed,
        rules=rules,
        online_config=online,
        profile=profile if profile is not None else LocalProfile(),
        accessibility=preferences,
    )
    presenter = Presenter(
        scale=preferred_scale() if scale is None else scale, caption=WINDOW_CAPTION
    )
    library = assets or ProceduralAssetLibrary(rules, theme.palette_for(preferences.contrast))
    renderer = Renderer(library, reduced_motion=preferences.reduced_motion)
    return ClientApp(
        shell,
        presenter,
        renderer,
        frame_cap=frame_cap,
        gamepads=gamepads if gamepads is not None else open_gamepads(),
    )


def parse_args(
    argv: Sequence[str] | None = None, *, profile: LocalProfile | None = None
) -> argparse.Namespace:
    """Parse the launch options, defaulting to what ``profile`` has saved.

    A launch option always wins over a saved value, because it was typed for this launch.
    What a saved profile changes is only what is used when nothing was typed, which is
    why it arrives as the argparse *default* rather than as a later override: ``--help``
    then prints the value the launch would actually use.

    Without a profile this is exactly the parser it has always been, with the shipped
    defaults, and a saved window scale is the one case with no fixed default to replace --
    absent a saved scale the client still measures the desktop and picks one.
    """
    settings = LocalSettings() if profile is None else profile.settings
    saved_scale = (
        profile.settings.scale if profile is not None and profile.settings_restored else None
    )
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
        default=saved_scale,
        choices=range(theme.MIN_SCALE, theme.MAX_SCALE + 1),
        help="whole-number window scale; the default is the saved one, or the desktop's",
    )
    parser.add_argument(
        "--frame-cap",
        type=_frame_cap,
        default=settings.frame_cap,
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
        default=settings.display_name,
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


def _launch_settings(options: argparse.Namespace, profile: LocalProfile) -> LocalSettings:
    """What this launch is actually running with, as a settings record.

    Adopted into the profile in memory and not written: a launch option is for one
    launch, and a client that saved every flag it was handed would make ``--name`` and
    ``--frame-cap`` permanent by accident. It matters anyway, because the file *is*
    written when the player changes the window scale in game, and the record written then
    should say what this session was running with rather than what the last one did.

    A launch option the settings record will not accept -- a roster name with a space in
    it, say -- leaves the saved settings alone rather than refusing to start. The option
    still reaches the online path, where it is checked and reported as it always was, and
    an offline launch has no business failing over a name nobody is going to read.
    """
    try:
        return LocalSettings(
            scale=profile.settings.scale if options.scale is None else options.scale,
            frame_cap=options.frame_cap,
            display_name=options.name,
        )
    except ValueError:
        return profile.settings


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
    profile = LocalProfile.load(ProfileStore())
    options = parse_args(argv, profile=profile)
    profile.adopt_settings(_launch_settings(options, profile))
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
                profile=profile,
            )
        except StageAdapterError as error:
            print(f"battle_city_client: {error}")
            return 1
        app.run()
    finally:
        pygame.display.quit()
        pygame.quit()
    return 0
