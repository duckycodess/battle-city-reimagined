"""What the asyncio shell needs from whatever is deciding things.

:class:`~battle_city_server.server.SessionServer` owns sockets, queues, deadlines and
writer tasks. It owns no rules at all: it reads a frame, hands the decoded message to an
authority, and delivers whatever that authority says to send. Until there was a lobby
there was exactly one authority, so the shell named it directly; now there are two
phases — a lobby deciding who is playing and what, and a game session deciding what
happened — and the shell must not care which one it is holding.

:class:`SessionAuthority` is that seam, stated as a protocol rather than a base class so
neither implementation inherits anything and both stay testable on their own.
:class:`~battle_city_server.session.GameSession` satisfies it without knowing it exists,
and :class:`~battle_city_server.lobby.MatchSession` satisfies it by being a lobby that
turns into a game session.

Two members are worth reading twice:

* :attr:`SessionAuthority.ticking` is how a lobby stops the clock. A session that has
  not started has no tick to run, and a shell that advanced one anyway would be
  simulating a match nobody agreed to — and would spin a core doing it. The shell waits
  instead.
* :meth:`SessionAuthority.disconnect` returns replies. A game session loses a player and
  says nothing to anyone, because the run carries on; a lobby loses a member and owes
  everyone else a new roster. One call, both behaviours.
"""

from __future__ import annotations

from typing import Protocol

from battle_city_protocol import (
    ClientMessage,
    ContentRef,
    RejectionCode,
    SessionClosed,
    StateSnapshot,
)
from battle_city_sim import SimulationState

from .config import SessionLimits
from .session import Reply


class SessionAuthority(Protocol):
    """Whatever decides what a connection may do and what a tick produced."""

    @property
    def session_id(self) -> str:
        """The identifier every message in this session carries."""

    @property
    def limits(self) -> SessionLimits:
        """The bounds a connected client can push against."""

    @property
    def content(self) -> ContentRef:
        """The content this session is running or is configured to run. For logs."""

    @property
    def rules_digest(self) -> str:
        """A digest of the rule constants a run would step with. For logs."""

    @property
    def closed(self) -> bool:
        """Whether the session is over."""

    @property
    def ticking(self) -> bool:
        """Whether there is a simulation to advance yet."""

    @property
    def tick(self) -> int:
        """The next tick to be simulated. Zero while no match has started."""

    @property
    def state(self) -> SimulationState:
        """The authoritative state.

        A lobby has none, and asking for one before a match starts raises rather than
        inventing an empty board.
        """

    def connect(self, identifier: int | None = None) -> int:
        """Register a connection that has proved nothing yet."""

    def disconnect(self, connection: int) -> tuple[Reply, ...]:
        """Forget ``connection``, and say anything the others are owed."""

    def slot_of(self, connection: int) -> int | None:
        """The slot ``connection`` holds, or ``None``."""

    def joined_slots(self) -> tuple[int, ...]:
        """Slots with a live connection in the *run*, ascending.

        Empty while no match has started: a lobby has members, which is a different
        thing from a session having players, and conflating the two is how a server
        ends up broadcasting a tick to somebody who never joined one.
        """

    def snapshot(self, *, keyframe: bool) -> StateSnapshot:
        """Describe the current authoritative state.

        Like :attr:`state`, this is a question only a running match can answer.
        """

    def handle(self, connection: int, message: ClientMessage) -> tuple[Reply, ...]:
        """Answer one decoded client message."""

    def advance_tick(self) -> tuple[Reply, ...]:
        """Run exactly one tick, if there is one to run."""

    def close(self, code: RejectionCode, detail: str) -> tuple[Reply, ...]:
        """End the session, telling every connection why."""

    def closing_notice(self, code: RejectionCode, detail: str = "") -> SessionClosed:
        """Build the message a connection is given as it is closed."""

    def rejection(
        self,
        connection: int,
        code: RejectionCode,
        detail: str = "",
        *,
        sequence: int | None = None,
    ) -> Reply:
        """Build a refusal, and record it."""
