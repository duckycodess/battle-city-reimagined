"""Protocol rejections.

Every refusal is a :class:`MessageError` carrying a stable
:class:`~battle_city_protocol.codes.RejectionCode`, so a caller can answer a bad message
with the code it already holds rather than mapping an exception type onto one. A
refusal is raised before any value is handed back, so a partially decoded message is
never observable.

``detail`` is a short human-readable string naming the field and the bound it broke. It
never quotes a field's value, because one of those fields is a membership token and a
detail is expected to reach a log.
"""

from __future__ import annotations

from .codes import RejectionCode
from .limits import MAX_DETAIL_LENGTH


class ProtocolError(Exception):
    """Base class for every protocol rejection."""


class MessageError(ProtocolError):
    """A message is not acceptable, with the code a peer should be told."""

    def __init__(self, code: RejectionCode, detail: str) -> None:
        self.code = code
        self.detail = detail[:MAX_DETAIL_LENGTH]
        super().__init__(f"{code.value}: {self.detail}")
