"""SDL joysticks, turned into the same control descriptors the bindings are written in.

This module and :mod:`battle_city_client.keymap` are the two places in the client that
know a device exists. It opens whatever pads SDL reports, follows them being plugged and
unplugged while the game runs, and turns axis, hat and button traffic into
:class:`~battle_city_client.accessibility.GamepadControl` values paired with a press or a
release. It decides nothing: which action a control drives is the binding set's answer,
and what that action means is the shell's.

Three things here are deliberate rather than incidental.

**The subsystem is guarded at launch.** ``pygame.joystick.init()`` fails on a machine
with no input subsystem at all, and a headless CI runner is exactly such a machine.
:func:`open_gamepads` returns a hub either way, and a hub that could not initialise is
simply a hub with no pads in it -- the keyboard path is untouched, and the client opens.

**A stick is a control only outside its dead zone.** SDL reports an axis continuously and
a resting stick is rarely exactly zero, so an unfiltered axis is a direction nobody is
pushing and a tank that drifts. :func:`~battle_city_client.accessibility.axis_direction`
bounds the reading, rejects a value that is not a number, and answers with one of three
values; this module only reports the *transitions* between them, so one push is one press
and one release, whatever the pad's reporting rate.

**A device that goes away releases what it was holding.** SDL stops delivering events for
an unplugged pad, including the release for a stick that was pushed when it was pulled
out, so the hub reports the removal and the loop neutralises exactly that device's held
input. A window that loses focus is the same case for the same reason, and both go
through :meth:`~battle_city_client.intents.HeldActions.clear_device` rather than clearing
everything, so a key the player is still holding on the keyboard is not released with it.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field

import pygame

from .accessibility import GamepadControl, GamepadControlKind, axis_direction

AXIS_SUB: int = 0
"""Sub-axis of a plain axis control. Only a hat has two, so an axis always uses zero."""


@dataclass(frozen=True, slots=True)
class ControlEvent:
    """One pad control going down or coming up, on one device."""

    device: int
    control: GamepadControl
    pressed: bool


@dataclass(slots=True)
class GamepadHub:
    """Every pad this client has open, and the last reading of each of their controls.

    ``available`` says whether SDL's joystick subsystem came up at all. A hub that is not
    available is still a perfectly usable object: it holds no pads, reports no events and
    is asked the same questions as one that is, which is what keeps the loop free of
    "if there is a pad" branches.
    """

    available: bool = False
    _pads: dict[int, pygame.joystick.JoystickType] = field(default_factory=dict, repr=False)
    _axes: dict[tuple[int, int], int] = field(default_factory=dict, repr=False)
    _hats: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict, repr=False)

    @property
    def device_count(self) -> int:
        """How many pads are open right now."""
        return len(self._pads)

    @property
    def devices(self) -> tuple[int, ...]:
        """The instance identifiers of the open pads, lowest first."""
        return tuple(sorted(self._pads))

    @property
    def names(self) -> tuple[str, ...]:
        """What the open pads call themselves, in device order, for the options screen."""
        return tuple(self._pads[device].get_name() for device in self.devices)

    # -- device life -----------------------------------------------------------

    def attach(self, index: int) -> int | None:
        """Open the pad at SDL device ``index``. Returns its instance id, or ``None``.

        A pad that refuses to open is skipped rather than fatal: one unusable device must
        not cost the player the others, or the keyboard.
        """
        try:
            pad = pygame.joystick.Joystick(index)
        except pygame.error:
            return None
        instance = int(pad.get_instance_id())
        if instance in self._pads:
            return instance
        self._pads[instance] = pad
        return instance

    def detach(self, instance: int) -> bool:
        """Forget the pad with this instance id, and every reading it had. Returns
        whether one was open.

        The readings go with it. A stick that was pushed when the pad was unplugged would
        otherwise still read as pushed if the same instance id came back.
        """
        pad = self._pads.pop(instance, None)
        self._axes = {key: value for key, value in self._axes.items() if key[0] != instance}
        self._hats = {key: value for key, value in self._hats.items() if key[0] != instance}
        if pad is None:
            return False
        # A pad that was physically removed is already gone as far as SDL is concerned,
        # which is the ordinary case here and not a failure. Nothing is left to release.
        with suppress(pygame.error):
            pad.quit()
        return True

    def forget_readings(self, instance: int) -> None:
        """Drop the remembered axis and hat positions for one device, keeping it open.

        Used when the window loses focus. The pad is still plugged in, but this window
        stops being told what it is doing, so the next reading after focus returns must
        be treated as new rather than compared against one taken minutes ago.
        """
        self._axes = {key: value for key, value in self._axes.items() if key[0] != instance}
        self._hats = {key: value for key, value in self._hats.items() if key[0] != instance}

    def forget_all_readings(self) -> None:
        """Drop every remembered axis and hat position, on every device.

        Used when the window loses focus. Deliberately not "every open pad": SDL
        delivers events for a device from the moment it reports one, and this hub reads
        a reading whether or not it got to open the pad behind it, so a readings table
        keyed off the open ones would miss exactly the device nobody opened.
        """
        self._axes.clear()
        self._hats.clear()

    def close(self) -> None:
        """Release every pad. Idempotent."""
        for instance in tuple(self._pads):
            self.detach(instance)

    # -- events ----------------------------------------------------------------

    def handle_event(
        self, event: pygame.event.Event, dead_zone_percent: int
    ) -> tuple[ControlEvent, ...]:
        """Translate one SDL joystick event into every transition it carries.

        A tuple rather than one event, and every release before every press. One SDL
        reading can be more than one transition: a stick thrown from one side straight
        past centre to the other is a release *and* a press, and a hat moved to a
        diagonal or flipped across it moves both of its axes at once. Returning the
        first of those and keeping the rest would be worse than returning none, because
        the hub has already recorded the new position -- the dropped transition is one
        the next reading compares equal to and never reports at all. That is exactly the
        bug this shape removes.

        Only transitions are reported. SDL reports a held stick many times a second, and
        turning each report into a press would make one push look like a hundred, so a
        reading that lands where the last one did produces nothing.

        The order is fixed, not incidental: releases first, then presses, and within each
        the horizontal axis before the vertical. A caller feeding these into held state
        would otherwise see a press and a release of the same control resolve by whichever
        came out first, and the client's rule is that press order decides a facing.
        """
        match event.type:
            case pygame.JOYBUTTONDOWN | pygame.JOYBUTTONUP:
                return (
                    ControlEvent(
                        device=int(event.instance_id),
                        control=GamepadControl(GamepadControlKind.BUTTON, int(event.button)),
                        pressed=event.type == pygame.JOYBUTTONDOWN,
                    ),
                )
            case pygame.JOYAXISMOTION:
                return self._axis_events(event, dead_zone_percent)
            case pygame.JOYHATMOTION:
                return self._hat_events(event)
            case _:
                return ()

    def released(self, instance: int) -> tuple[GamepadControl, ...]:
        """Every control this device was last seen holding, for neutralising it.

        Buttons are absent on purpose: a button's release is an event SDL delivers, and
        the loop drops the whole device's held input when the device goes, so there is
        nothing a remembered button state would add. Sticks and hats are remembered here
        because their "release" is a reading rather than an event.
        """
        held: list[GamepadControl] = []
        for (device, axis), direction in sorted(self._axes.items()):
            if device == instance and direction != 0:
                held.append(GamepadControl(GamepadControlKind.AXIS, axis, direction))
        for (device, hat), (x, y) in sorted(self._hats.items()):
            if device != instance:
                continue
            if x:
                held.append(GamepadControl(GamepadControlKind.HAT, hat, x, 0))
            if y:
                held.append(GamepadControl(GamepadControlKind.HAT, hat, y, 1))
        return tuple(sorted(held, key=lambda control: control.sort_key))

    def _axis_events(
        self, event: pygame.event.Event, dead_zone_percent: int
    ) -> tuple[ControlEvent, ...]:
        """Every transition one stick reading carries, release first.

        A stick thrown from one side straight to the other skips centre, and SDL may
        well never report the reading in between: the only place both halves can be
        reported is here, from the one event that crossed.
        """
        instance = int(event.instance_id)
        axis = int(event.axis)
        direction = axis_direction(float(event.value), dead_zone_percent)
        previous = self._axes.get((instance, axis), 0)
        if direction == previous:
            return ()
        self._axes[(instance, axis)] = direction
        return _transitions(
            instance, GamepadControlKind.AXIS, axis, 0, before=previous, after=direction
        )

    def _hat_events(self, event: pygame.event.Event) -> tuple[ControlEvent, ...]:
        """Every transition one hat reading carries, releases first, horizontal first.

        A hat moves both of its axes in one reading whenever it goes to a diagonal,
        leaves one, or flips across the centre, and a hat released from a diagonal
        releases two controls at once. All of them come out of this one event.
        """
        instance = int(event.instance_id)
        hat = int(event.hat)
        # SDL's hat reports up as +1 and the client's screen coordinates grow downward,
        # so the vertical component is flipped here, once, rather than in each binding.
        after = (int(event.value[0]), -int(event.value[1]))
        before = self._hats.get((instance, hat), (0, 0))
        if after == before:
            return ()
        self._hats[(instance, hat)] = after
        return tuple(
            transition
            for sub_axis in (0, 1)
            for transition in _transitions(
                instance,
                GamepadControlKind.HAT,
                hat,
                sub_axis,
                before=before[sub_axis],
                after=after[sub_axis],
            )
        )


def _transitions(
    device: int,
    kind: GamepadControlKind,
    index: int,
    sub_axis: int,
    *,
    before: int,
    after: int,
) -> tuple[ControlEvent, ...]:
    """One axis going from ``before`` to ``after``, as a release and then a press.

    Zero to a side is one press, a side to zero is one release, and a side straight to
    the other side is both -- in that order, so a caller that feeds these into held
    state never has a press undone by the release it arrived with.
    """
    events: list[ControlEvent] = []
    if before != 0:
        events.append(
            ControlEvent(
                device=device,
                control=GamepadControl(kind, index, before, sub_axis),
                pressed=False,
            )
        )
    if after != 0:
        events.append(
            ControlEvent(
                device=device,
                control=GamepadControl(kind, index, after, sub_axis),
                pressed=True,
            )
        )
    return tuple(events)


def open_gamepads() -> GamepadHub:
    """Bring up SDL's joystick subsystem and open whatever is already plugged in.

    Never raises. A machine with no joystick subsystem, no pads, or a pad that will not
    open gets a hub that reports nothing, and the client runs on the keyboard exactly as
    it did before this module existed.
    """
    hub = GamepadHub()
    try:
        pygame.joystick.init()
    except pygame.error:
        return hub
    hub.available = True
    try:
        count = pygame.joystick.get_count()
    except pygame.error:
        return hub
    for index in range(count):
        hub.attach(index)
    return hub


def close_gamepads(hub: GamepadHub) -> None:
    """Release the pads and the subsystem. Idempotent, and never raises."""
    hub.close()
    if not hub.available:
        return
    try:
        pygame.joystick.quit()
    except pygame.error:
        return
    hub.available = False
