"""Watching an authoritative session without playing in it.

A spectator is an *output* seam and nothing else. It is handed the same
:class:`~battle_city_protocol.StateSnapshot` and :class:`~battle_city_protocol.TickEvents`
the players are sent, it is told when the session opened and when it closed, and that is
the entire surface. There is no method here that returns a
:class:`~battle_city_protocol.Reply`, no method that accepts a
:class:`~battle_city_protocol.ClientMessage`, and no way to reach
:meth:`~battle_city_server.session.GameSession.handle` or
:meth:`~battle_city_server.session.GameSession.schedule_commands` through one.

That is deliberate and it is the whole point of the issue this landed under: a spectator
takes no player slot, holds no credential, and has nothing to say. The networking
specification's authority rules are kept by the shape of this seam rather than by a check
somewhere that could be skipped — an observer cannot submit input because there is no
method through which it could.

In process only, for now
------------------------
Registration is a direct call on a running :class:`~battle_city_server.session.GameSession`,
so an observer is something the process already holds: a recorder, a diagnostic, a local
spectator window. Admitting a spectator over a socket needs a message to admit one with,
an admission rule, and a decision about what a spectator may see of a competitive match
in progress — three things that are a protocol change and a product decision rather than
this seam. Until that lands, no spectator arrives from a network at all, which is the
conservative end of that question rather than a guess at it.

Failure is the observer's, never the session's
----------------------------------------------
An observer that raises is removed and reported; the tick it raised during still
completed and every player still got it. A read-only watcher that could stall the
authority would not be read-only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol, runtime_checkable

from battle_city_protocol import (
    SessionClosed,
    SessionInfo,
    StateSnapshot,
    TickEvents,
)

DEFAULT_MAX_OBSERVERS: Final[int] = 4
"""Observers one session will hold at once.

Finite because each one is handed every tick's snapshot, so an unbounded roster is an
unbounded amount of work per tick inside the loop that owes every player a cadence.
"""


class ObserverRefusedError(ValueError):
    """A session would not register an observer."""


class TooManyObserversError(ObserverRefusedError):
    """A session was asked to hold more observers than its limit allows."""


@runtime_checkable
class SessionObserver(Protocol):
    """Everything a spectator is told, and everything it can do.

    Stated as a :class:`~typing.Protocol` so an implementation inherits nothing and the
    client's spectator view can satisfy it without the client package depending on the
    server package, which the architecture specification does not allow.
    """

    def opened(self, info: SessionInfo, snapshot: StateSnapshot) -> None:
        """The session's terms and a keyframe of the state as it stands right now.

        Delivered once, at registration, so a watcher that arrives mid-run starts from a
        complete board rather than from whatever the next partial snapshot happens to
        carry.
        """

    def observed(self, snapshot: StateSnapshot, events: TickEvents | None) -> None:
        """One simulated tick: the authoritative snapshot and the events it produced.

        ``events`` is ``None`` for a tick that produced none, which is the common case
        and is not the same as a tick that was not delivered.
        """

    def closed(self, notice: SessionClosed) -> None:
        """The session ended, with the reason every connection was given."""


class ObserverRegistry:
    """The observers one session is delivering to, and the bound on how many.

    Delivery order is registration order, which is stable and does not depend on the
    iteration order of any unordered collection.
    """

    __slots__ = ("_limit", "_observers", "_report")

    def __init__(
        self,
        limit: int = DEFAULT_MAX_OBSERVERS,
        *,
        report: Callable[[str], None] | None = None,
    ) -> None:
        if limit < 0:
            raise ValueError("observer limit must not be negative")
        self._limit = limit
        self._observers: list[SessionObserver] = []
        self._report = report

    def __len__(self) -> int:
        return len(self._observers)

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def observers(self) -> tuple[SessionObserver, ...]:
        """The registered observers, in delivery order. For diagnostics."""
        return tuple(self._observers)

    def add(self, observer: SessionObserver) -> None:
        """Register ``observer``. Registering the same one twice is refused.

        "The same one" means the same object, compared with ``is``. An observer is a
        thing with a position in a run, not a value: two equal-but-distinct watchers are
        two watchers, and ``==`` would refuse the second one and then, on removal, drop
        whichever of the two happened to be first in the list.
        """
        if self._registered(observer):
            raise TooManyObserversError("that observer is already registered")
        if len(self._observers) >= self._limit:
            raise TooManyObserversError(f"this session holds at most {self._limit} observers")
        self._observers.append(observer)

    def remove(self, observer: SessionObserver) -> None:
        """Forget ``observer``, by identity. Removing an unregistered one is fine."""
        self._observers = [existing for existing in self._observers if existing is not observer]

    def _registered(self, observer: SessionObserver) -> bool:
        return any(existing is observer for existing in self._observers)

    def opened(self, observer: SessionObserver, info: SessionInfo, snapshot: StateSnapshot) -> None:
        """Hand one newly registered observer the session's terms and a keyframe."""
        try:
            observer.opened(info, snapshot)
        except Exception as error:  # a watcher must not be able to break registration
            self._dropped(observer, error)

    def observed(self, snapshot: StateSnapshot, events: TickEvents | None) -> None:
        """Hand every observer one simulated tick."""
        for observer in tuple(self._observers):
            try:
                observer.observed(snapshot, events)
            except Exception as error:  # a watcher must not be able to break the tick
                self._dropped(observer, error)

    def closed(self, notice: SessionClosed) -> None:
        """Tell every observer the session ended."""
        for observer in tuple(self._observers):
            try:
                observer.closed(notice)
            except Exception as error:  # a watcher must not be able to break the ending
                self._dropped(observer, error)

    def _dropped(self, observer: SessionObserver, error: Exception) -> None:
        """Forget an observer that raised, and report it without naming the observer."""
        self.remove(observer)
        if self._report is not None:
            self._report(f"observer dropped after {type(error).__name__}")
