"""Validate the bytes about to be written, using the loader the game itself uses.

There is no second validator here, and that is the point. A level is checked by writing
the exact payload to a scratch file and handing it to
:func:`battle_city_content.load_level`; a pack is checked by staging the whole pack and
handing the manifest to :func:`battle_city_content.load_pack`. Anything these tools
accept is therefore something the game accepts, and no rule can drift between the editor
and the loader because there is only one copy of it.

Two details make that honest rather than merely convenient:

* **The scratch path never escapes.** The loader reports the file it read, which is a
  temporary one the author has never heard of. Every diagnostic is remapped to the
  document the author is actually working on before it leaves this module, and the
  returned :class:`~battle_city_content.Level` has its ``origin`` remapped with it.
* **The scratch directory is removed either way.** It is a context manager, so a
  rejection cleans up exactly like an acceptance.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from battle_city_content import ContentError, ContentValidationError, Level, load_level

from .errors import Diagnostic, DocumentInvalid

SCRATCH_PREFIX: str = "battle-city-validate-"
"""Prefix for the throwaway directory a validation runs in."""


def validate_level_bytes(payload: bytes, *, document: str) -> Level:
    """Validate ``payload`` as a level file named ``document``.

    ``document`` is a label, not a path that gets written: it is what every diagnostic
    and the returned record's ``origin`` will say. Raises :class:`DocumentInvalid`.
    """
    with tempfile.TemporaryDirectory(prefix=SCRATCH_PREFIX) as scratch:
        staged = Path(scratch) / "level.json"
        staged.write_bytes(payload)
        try:
            level = load_level(staged)
        except ContentValidationError as error:
            raise DocumentInvalid(diagnostic_for(error, {staged: document}, document)) from error
        except ContentError as error:
            raise DocumentInvalid(Diagnostic(document, "", str(error))) from error
    return replace(level, origin=Path(document))


def diagnostic_for(
    error: ContentValidationError,
    labels: Mapping[Path, str],
    fallback: str,
) -> Diagnostic:
    """Rewrite a loader error so it names the author's document, not the scratch copy.

    ``load_level`` resolves the path it was handed, so the error can carry either the
    path as staged or its fully resolved form -- a temporary directory under a symlinked
    ``/tmp`` gives the second. Both spellings are looked up before falling back.
    """
    label = labels.get(error.path)
    if label is None:
        label = labels.get(error.path.resolve())
    if label is None:
        label = _resolved_lookup(error.path, labels)
    return Diagnostic(document=label or fallback, field=error.field, message=error.message)


def pack_diagnostic(error: ContentValidationError, labels: Mapping[Path, str]) -> Diagnostic:
    """As :func:`diagnostic_for`, falling back to the path itself for an unknown file."""
    return diagnostic_for(error, labels, str(error.path))


def _resolved_lookup(path: Path, labels: Mapping[Path, str]) -> str | None:
    """Match ``path`` against resolved keys, for a staging root behind a symlink."""
    resolved = path.resolve()
    for candidate, label in labels.items():
        if candidate.resolve() == resolved:
            return label
    return None
