"""What the asset pipeline raises, and the diagnostic it carries.

The split mirrors :mod:`battle_city_tools.errors`. :class:`AssetInvalid` says a checked-in
artifact is wrong and names the file and the field inside it, so a failure reads as a
location rather than a traceback. :class:`AssetRefusal` says the artifact may be fine but
the command will not do what was asked -- writing over a tracked file nobody named, for
instance -- which is not a validation failure and must not be silenced by passing a flag.
"""

from __future__ import annotations

from dataclasses import dataclass


class AssetError(Exception):
    """Base class for every rejection the asset pipeline raises."""


@dataclass(frozen=True, slots=True)
class AssetDiagnostic:
    """One asset problem, located.

    ``artifact`` names the file the reader is looking at, ``field`` is the dotted path
    inside it -- a frame name, a metadata key -- and is empty when the whole artifact is
    at fault, and ``message`` says what is wrong in the pipeline's own wording.
    """

    artifact: str
    field: str
    message: str

    def __str__(self) -> str:
        if self.field:
            return f"{self.artifact}: {self.field}: {self.message}"
        return f"{self.artifact}: {self.message}"


class AssetInvalid(AssetError):
    """An artifact is malformed, inconsistent with its metadata, or unreadable."""

    def __init__(self, diagnostic: AssetDiagnostic) -> None:
        super().__init__(str(diagnostic))
        self.diagnostic = diagnostic


class AssetRefusal(AssetError):
    """The command will not do what was asked, and passing a flag is the wrong answer."""


def invalid(artifact: str, field: str, message: str) -> AssetInvalid:
    """Build an :class:`AssetInvalid` without assembling the diagnostic at every call."""
    return AssetInvalid(AssetDiagnostic(artifact=artifact, field=field, message=message))
