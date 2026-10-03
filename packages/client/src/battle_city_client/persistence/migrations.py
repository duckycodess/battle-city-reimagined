"""Pure, deterministic upgrades from one save schema version to the next.

A migration is a function from one version's payload to the next version's payload, and
a registry is the set of those functions keyed by the version each one upgrades *from*.
Upgrading a document is then walking the chain: ``migrate`` applies step ``n``, then
``n + 1``, up to the version this build reads. Nothing here touches a file, a clock or
anything outside its arguments, so a chain is testable on its own and two runs over the
same bytes produce the same payload.

Why the shipped registries are empty
------------------------------------
This is the first build that writes anything, so every document it can meet is already
at its current version and there is no earlier layout to upgrade from. Inventing one --
shipping a "version 0" the game never wrote, with a step that converts it -- would be a
fabricated format: the step could never run against a real file, and a test exercising it
would be proving that a fiction converts to another fiction.

The machinery is still real, still shipped and still tested, because the moment that
matters is the *first* schema change, and a registry written then is a registry written
under time pressure against existing player data. ``tests/persistence`` builds synthetic
registries and synthetic old documents and drives them through the same ``migrate`` and
the same store the game uses, so the chain, the ordering, the missing-step refusal and
the backup-before-upgrade behaviour are all exercised before anyone depends on them.

What a step may and may not do
------------------------------
A step receives the previous version's ``data`` mapping and returns the next version's.
It must be pure and total over documents that this project actually wrote at that
version: no I/O, no randomness, no clock, and no reaching for the current defaults, since
the defaults will have moved on by the time the step runs. A step that cannot convert a
payload raises :class:`MigrationFailed`, and the store then treats the file exactly as it
treats a corrupt one -- defaults in memory, the original left untouched on disk.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from battle_city_protocol import JsonValue

from .documents import SaveError

type SavePayload = Mapping[str, JsonValue]
type MigrationStep = Callable[[SavePayload], dict[str, JsonValue]]
type MigrationRegistry = Mapping[int, MigrationStep]
"""Steps keyed by the schema version they upgrade *from*; each produces that version + 1."""

type MigrationTable = Mapping[str, MigrationRegistry]
"""Registries by document kind."""


class MigrationFailed(SaveError):
    """A document is an older version this build has no complete path forward from."""


NO_MIGRATIONS: Final[MigrationRegistry] = {}
"""The registry of a kind whose only version is its current one."""


def migrate(
    data: SavePayload, *, from_version: int, to_version: int, steps: MigrationRegistry
) -> dict[str, JsonValue]:
    """Walk ``data`` from ``from_version`` up to ``to_version`` through ``steps``.

    Applying no steps is a legitimate answer: a document already at ``to_version`` comes
    back unchanged, as a copy, so a caller cannot mutate the mapping it was handed.

    Raises :class:`MigrationFailed` when a version in the chain has no step, and when the
    chain is asked to run backwards -- a newer document is a recovery case rather than a
    migration, and downgrading it would mean discarding whatever the newer build recorded.
    """
    if from_version > to_version:
        raise MigrationFailed(
            f"cannot migrate a schema version {from_version} document down to {to_version}"
        )
    payload = dict(data)
    for version in range(from_version, to_version):
        step = steps.get(version)
        if step is None:
            raise MigrationFailed(f"no migration from schema version {version} to {version + 1}")
        payload = dict(step(payload))
    return payload
