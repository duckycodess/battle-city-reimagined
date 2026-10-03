"""Schema migration: the chain, its ordering, and what happens when a link is missing.

This build has shipped nothing to migrate from, and the shipped registries are therefore
empty. That is a fact about the game's history, not about the machinery, so the machinery
is exercised here against *synthetic* versions: a registry this test writes, and documents
in layouts the game never shipped. Nothing here pretends a legacy format existed.

What is being proven is the part that will matter the first time a schema does change,
when it will be written under pressure against real player files: that steps run in order,
that the result is a function of the input alone, that a gap in the chain is refused
rather than skipped, and that running a chain twice gives the same answer.
"""

from __future__ import annotations

import pytest
from battle_city_client.persistence import (
    SETTINGS_SCHEMA_VERSION,
    MigrationFailed,
    MigrationRegistry,
    SavePayload,
    migrate,
)
from battle_city_client.persistence.store import MIGRATIONS
from battle_city_protocol import JsonValue


def _rename_to_scale(data: SavePayload) -> dict[str, JsonValue]:
    """Synthetic v1 -> v2: a field that used to be called something else."""
    renamed = dict(data)
    renamed["scale"] = renamed.pop("zoom", 1)
    return renamed


def _add_frame_cap(data: SavePayload) -> dict[str, JsonValue]:
    """Synthetic v2 -> v3: a field that did not exist before, with a stated default."""
    return {**data, "frame_cap": 60}


CHAIN: MigrationRegistry = {1: _rename_to_scale, 2: _add_frame_cap}


def test_the_shipped_registries_are_empty_because_nothing_shipped_before_this() -> None:
    assert all(not steps for steps in MIGRATIONS.values())
    assert SETTINGS_SCHEMA_VERSION == 1


def test_a_document_already_at_the_target_version_is_returned_unchanged() -> None:
    data = {"scale": 3}
    assert migrate(data, from_version=3, to_version=3, steps=CHAIN) == data


def test_the_result_is_a_copy_the_caller_cannot_mutate_through() -> None:
    original = {"scale": 3}
    migrated = migrate(original, from_version=3, to_version=3, steps=CHAIN)
    migrated["scale"] = 8
    assert original == {"scale": 3}


def test_steps_run_in_version_order() -> None:
    migrated = migrate({"zoom": 4}, from_version=1, to_version=3, steps=CHAIN)
    assert migrated == {"scale": 4, "frame_cap": 60}


def test_one_step_at_a_time_reaches_the_same_place_as_the_whole_chain() -> None:
    once = migrate({"zoom": 4}, from_version=1, to_version=3, steps=CHAIN)
    first = migrate({"zoom": 4}, from_version=1, to_version=2, steps=CHAIN)
    twice = migrate(first, from_version=2, to_version=3, steps=CHAIN)
    assert once == twice


def test_migrating_is_deterministic() -> None:
    first = migrate({"zoom": 4}, from_version=1, to_version=3, steps=CHAIN)
    second = migrate({"zoom": 4}, from_version=1, to_version=3, steps=CHAIN)
    assert first == second


def test_a_missing_step_refuses_rather_than_skipping_a_version() -> None:
    gapped: MigrationRegistry = {1: _rename_to_scale}
    with pytest.raises(MigrationFailed, match="version 2 to 3"):
        migrate({"zoom": 4}, from_version=1, to_version=3, steps=gapped)


def test_a_chain_with_no_steps_at_all_refuses() -> None:
    with pytest.raises(MigrationFailed):
        migrate({"zoom": 4}, from_version=1, to_version=2, steps={})


def test_a_newer_document_is_never_migrated_downwards() -> None:
    """Downgrading would mean discarding whatever the newer build recorded."""
    with pytest.raises(MigrationFailed, match="down to"):
        migrate({"scale": 3}, from_version=4, to_version=1, steps=CHAIN)
