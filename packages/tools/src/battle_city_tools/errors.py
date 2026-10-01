"""What these tools raise, and the diagnostic they carry.

Two failures, kept apart because they mean different things to whoever ran the command.

:class:`DocumentInvalid` says the content is wrong: it carries a :class:`Diagnostic` with
the document, the field inside it and the loader's own message, so a caller can point at
a line rather than at a file. :class:`ToolRefusal` says the content may well be fine but
the tool will not do what was asked -- writing into the bundled pack, clobbering a file
nobody named, overwriting the very level being read. Those are not validation failures
and reporting them as such would teach an author to re-run with ``--force`` reflexively.
"""

from __future__ import annotations

from dataclasses import dataclass


class ToolsError(Exception):
    """Base class for every rejection these tools raise."""


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One content problem, located.

    ``document`` names the file the author is editing -- never the temporary copy the
    validator actually read -- ``field`` is the dotted path inside it, empty when the
    whole document is at fault, and ``message`` is the loader's own wording.
    """

    document: str
    field: str
    message: str

    def __str__(self) -> str:
        return (
            f"{self.document}: {self.field}: {self.message}"
            if self.field
            else f"{self.document}: {self.message}"
        )


class DocumentInvalid(ToolsError):
    """A document these tools produced or read does not satisfy the content contract."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(str(diagnostic))


class ToolRefusal(ToolsError):
    """The tool declined to act. The content is not what is being complained about."""
