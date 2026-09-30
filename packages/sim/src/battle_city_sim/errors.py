"""Error types raised by the deterministic simulation.

Every error is a plain value error: the simulation never partially applies a tick and
never repairs malformed data on the caller's behalf.
"""

from __future__ import annotations


class SimulationError(Exception):
    """Base class for every simulation rejection."""


class StageValidationError(SimulationError):
    """A stage definition violates the content contract.

    The message always names the offending field and, where applicable, the grid
    coordinate so callers can report ``file: field`` style diagnostics.
    """


class InvalidInputError(SimulationError):
    """A tick input is not legal for the state it was submitted against.

    Raised before any part of the tick is applied, so the caller's state object is
    still the exact pre-tick state after the exception propagates.
    """
