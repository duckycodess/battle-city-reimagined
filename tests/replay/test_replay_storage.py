"""Keeping a recording on disk: atomic replacement, bounded growth, bounded reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from battle_city_client.persistence import (
    MAX_STORED_REPLAYS,
    REPLAY_DIRECTORY,
    ReplayLibrary,
    ReplayNameError,
)
from battle_city_protocol import MAX_REPLAY_BYTES, encode_replay
from replay_helpers import as_json, minimal_document, to_bytes


def library(tmp_path: Path) -> ReplayLibrary:
    return ReplayLibrary(lambda: tmp_path)


def test_a_saved_replay_reads_back_unchanged(tmp_path: Path) -> None:
    store = library(tmp_path)
    document = minimal_document()
    assert store.save("run-one", document) == ""
    assert store.load("run-one").value == document


def test_a_saved_replay_lands_in_the_replay_directory(tmp_path: Path) -> None:
    store = library(tmp_path)
    store.save("run-one", minimal_document())
    assert store.directory == tmp_path / REPLAY_DIRECTORY
    assert store.names() == ("run-one",)


def test_a_replace_leaves_no_staging_file_behind(tmp_path: Path) -> None:
    store = library(tmp_path)
    store.save("run-one", minimal_document())
    store.save("run-one", minimal_document())
    assert [path.name for path in store.directory.iterdir()] == ["run-one.replay.json"]


def test_a_missing_replay_is_not_a_failure(tmp_path: Path) -> None:
    loaded = library(tmp_path).load("never-recorded")
    assert loaded.value is None
    assert loaded.notice == ""


def test_an_unreadable_replay_is_reported_and_left_alone(tmp_path: Path) -> None:
    store = library(tmp_path)
    store.save("run-one", minimal_document())
    path = store.path_for("run-one")
    path.write_bytes(b"{not json")
    loaded = store.load("run-one")
    assert loaded.value is None
    assert "UNUSABLE" in loaded.notice
    assert not loaded.writable
    assert path.read_bytes() == b"{not json"


def test_a_replay_carrying_a_credential_is_refused_on_the_way_in(tmp_path: Path) -> None:
    store = library(tmp_path)
    store.save("run-one", minimal_document())
    body = as_json(minimal_document())
    body["metadata"]["token"] = "secret"
    store.path_for("run-one").write_bytes(to_bytes(body))
    loaded = store.load("run-one")
    assert loaded.value is None
    assert "CREDENTIAL_FIELD" in loaded.notice


def test_an_oversized_file_is_refused_without_being_parsed(tmp_path: Path) -> None:
    store = library(tmp_path)
    store.save("run-one", minimal_document())
    store.path_for("run-one").write_bytes(b"x" * (MAX_REPLAY_BYTES + 64))
    loaded = store.load("run-one")
    assert loaded.value is None
    assert loaded.notice == "REPLAY IS TOO LARGE"


def test_the_directory_will_not_grow_past_its_bound(tmp_path: Path) -> None:
    store = library(tmp_path)
    for index in range(MAX_STORED_REPLAYS):
        assert store.save(f"run-{index}", minimal_document()) == ""
    notice = store.save("one-too-many", minimal_document())
    assert "FULL" in notice
    assert len(store.names()) == MAX_STORED_REPLAYS


def test_replacing_an_existing_recording_is_allowed_at_the_bound(tmp_path: Path) -> None:
    store = library(tmp_path)
    for index in range(MAX_STORED_REPLAYS):
        store.save(f"run-{index}", minimal_document())
    assert store.save("run-0", minimal_document()) == ""


def test_a_recording_can_be_removed(tmp_path: Path) -> None:
    store = library(tmp_path)
    store.save("run-one", minimal_document())
    assert store.delete("run-one") == ""
    assert store.names() == ()
    assert store.delete("run-one") == ""


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".hidden", "with space"])
def test_a_name_that_is_not_an_identifier_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ReplayNameError):
        library(tmp_path).path_for(name)


def test_nothing_is_written_before_the_document_encodes(tmp_path: Path) -> None:
    """A document the protocol refuses never reaches the disk."""
    store = library(tmp_path)
    store.save("run-one", minimal_document())
    original = store.path_for("run-one").read_bytes()
    assert json.loads(original.decode("utf-8")) == json.loads(
        encode_replay(minimal_document()).decode("utf-8")
    )
