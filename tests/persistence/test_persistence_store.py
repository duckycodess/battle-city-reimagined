"""The two dangerous moments: replacing a save, and reading a bad one back.

The directory is always a ``tmp_path``. Nothing in this file resolves the real profile
location, and :func:`battle_city_client.persistence.default_save_dir` is only ever asked
what it would answer, never acted on.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from battle_city_client.persistence import (
    SAVE_DIR_ENV,
    SETTINGS_KIND,
    SETTINGS_SCHEMA_VERSION,
    Document,
    Loaded,
    LocalSettings,
    MigrationTable,
    ProfileStore,
    SavePayload,
    default_save_dir,
    encode_document,
    settings_from_data,
)
from battle_city_client.persistence import store as store_module
from battle_city_protocol import JsonValue

CURRENT = SETTINGS_SCHEMA_VERSION


def _store(directory: Path, *, migrations: MigrationTable | None = None) -> ProfileStore:
    if migrations is None:
        return ProfileStore(lambda: directory)
    return ProfileStore(lambda: directory, migrations=migrations)


def _load(store: ProfileStore, *, current_version: int = CURRENT) -> Loaded[LocalSettings]:
    return store.load(SETTINGS_KIND, current_version=current_version, parse=settings_from_data)


def _write(path: Path, data: Mapping[str, JsonValue], *, version: int = CURRENT) -> bytes:
    payload = encode_document(Document(kind=SETTINGS_KIND, schema_version=version, data=data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


# -- the path -----------------------------------------------------------------


def test_the_save_directory_is_resolved_lazily_and_only_once(tmp_path: Path) -> None:
    calls = 0

    def locate() -> Path:
        nonlocal calls
        calls += 1
        return tmp_path

    store = ProfileStore(locate)
    assert calls == 0
    assert store.directory == tmp_path
    assert store.path_for(SETTINGS_KIND) == tmp_path / "settings.json"
    assert calls == 1


def test_the_environment_decides_where_the_profile_lives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(SAVE_DIR_ENV, str(tmp_path / "explicit"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_save_dir() == tmp_path / "explicit"

    monkeypatch.delenv(SAVE_DIR_ENV)
    assert default_save_dir() == tmp_path / "xdg" / store_module.APPLICATION_DIRECTORY

    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert default_save_dir() == tmp_path / "appdata" / store_module.APPLICATION_DIRECTORY


def test_deciding_where_the_profile_lives_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(SAVE_DIR_ENV, str(tmp_path / "unborn"))
    assert not default_save_dir().exists()


# -- reading ------------------------------------------------------------------


def test_no_file_yet_is_not_a_failure_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path / "profile")
    loaded = store.load(SETTINGS_KIND, current_version=CURRENT, parse=settings_from_data)
    assert loaded.value is None
    assert loaded.notice == ""
    assert loaded.writable
    assert not (tmp_path / "profile").exists()


def test_a_saved_document_round_trips_through_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    settings = LocalSettings(scale=5, frame_cap=45, display_name="ducky")
    assert store.save(SETTINGS_KIND, version=CURRENT, data=settings.to_data()) == ""
    loaded = store.load(SETTINGS_KIND, current_version=CURRENT, parse=settings_from_data)
    assert loaded.value == settings
    assert loaded.writable


def test_a_corrupt_file_gives_defaults_a_notice_and_no_permission_to_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b"{ this is not a document")
    loaded = _load(_store(tmp_path))
    assert loaded.value is None
    assert "UNREADABLE" in loaded.notice
    assert not loaded.writable
    assert path.read_bytes() == b"{ this is not a document"


def test_a_file_from_a_newer_build_is_reported_as_such_and_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = _write(path, {"anything": "a later build understands"}, version=CURRENT + 3)
    loaded = _load(_store(tmp_path))
    assert loaded.value is None
    assert "NEWER BUILD" in loaded.notice
    assert not loaded.writable
    assert path.read_bytes() == original


def test_a_structurally_valid_file_with_an_unusable_value_is_a_recovery_case(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    original = _write(path, {"display_name": "player", "frame_cap": 120, "scale": 99})
    loaded = _load(_store(tmp_path))
    assert loaded.value is None
    assert not loaded.writable
    assert path.read_bytes() == original


def test_an_unreadable_file_does_not_raise_into_the_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path / "settings.json", LocalSettings().to_data())

    def refuse(self: Path, *args: object, **kwargs: object) -> bytes:
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    loaded = _load(_store(tmp_path))
    assert loaded.value is None
    assert "COULD NOT BE READ" in loaded.notice
    assert not loaded.writable


# -- writing ------------------------------------------------------------------


def test_a_write_creates_the_directory_and_leaves_no_temporary_behind(tmp_path: Path) -> None:
    directory = tmp_path / "deep" / "profile"
    store = _store(directory)
    assert store.save(SETTINGS_KIND, version=CURRENT, data=LocalSettings().to_data()) == ""
    assert sorted(path.name for path in directory.iterdir()) == ["settings.json"]


def test_a_write_that_fails_half_way_leaves_the_previous_save_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failure a save exists to survive: the process dies between open and rename."""
    store = _store(tmp_path)
    store.save(SETTINGS_KIND, version=CURRENT, data=LocalSettings(scale=2).to_data())
    original = (tmp_path / "settings.json").read_bytes()

    def interrupted(source: object, target: object) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(os, "replace", interrupted)
    notice = store.save(SETTINGS_KIND, version=CURRENT, data=LocalSettings(scale=7).to_data())

    assert "COULD NOT BE WRITTEN" in notice
    assert (tmp_path / "settings.json").read_bytes() == original
    assert not (tmp_path / "settings.json.tmp").exists()


def test_a_temporary_left_by_an_earlier_kill_is_inert(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(SETTINGS_KIND, version=CURRENT, data=LocalSettings(scale=2).to_data())
    leftover = tmp_path / "settings.json.tmp"
    leftover.write_bytes(b"half a document")

    loaded = _load(store)
    assert loaded.value == LocalSettings(scale=2)

    store.save(SETTINGS_KIND, version=CURRENT, data=LocalSettings(scale=4).to_data())
    assert not leftover.exists()
    reloaded = _load(store)
    assert reloaded.value == LocalSettings(scale=4)


# -- migration ----------------------------------------------------------------
#
# Synthetic throughout: the registry, the old layout and the target version are written
# here. This build has shipped no earlier schema, and inventing one would prove nothing.


def _add_display_name(data: SavePayload) -> dict[str, JsonValue]:
    return {**data, "display_name": "player"}


NEXT = CURRENT + 1
UPGRADE: MigrationTable = {SETTINGS_KIND: {CURRENT: _add_display_name}}


def test_an_older_file_is_backed_up_before_it_is_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = _write(path, {"frame_cap": 90, "scale": 2})
    store = _store(tmp_path, migrations=UPGRADE)

    loaded = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)

    assert loaded.value == LocalSettings(scale=2, frame_cap=90, display_name="player")
    assert "UPGRADED" in loaded.notice
    assert loaded.writable
    backup = tmp_path / f"settings.json.v{CURRENT}.bak"
    assert backup.read_bytes() == original
    assert path.read_bytes() != original
    assert b'"schema_version":2' in path.read_bytes()


def test_an_upgraded_file_is_not_upgraded_again(tmp_path: Path) -> None:
    _write(tmp_path / "settings.json", {"frame_cap": 90, "scale": 2})
    store = _store(tmp_path, migrations=UPGRADE)
    first = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)
    second = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)
    assert second.value == first.value
    assert second.notice == ""


def test_an_older_file_with_no_path_forward_is_treated_as_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = _write(path, {"frame_cap": 90, "scale": 2})
    store = _store(tmp_path, migrations={SETTINGS_KIND: {}})

    loaded = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)

    assert loaded.value is None
    assert "CANNOT BE UPGRADED" in loaded.notice
    assert not loaded.writable
    assert path.read_bytes() == original
    assert not (tmp_path / f"settings.json.v{CURRENT}.bak").exists()


def test_an_upgrade_is_abandoned_when_the_backup_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never discard a recoverable prior save: no backup, no upgrade."""
    path = tmp_path / "settings.json"
    original = _write(path, {"frame_cap": 90, "scale": 2})

    def refuse(target: Path, payload: bytes) -> None:
        raise OSError("no room for a backup")

    monkeypatch.setattr(store_module, "_write_new_file", refuse)
    store = _store(tmp_path, migrations=UPGRADE)
    loaded = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)

    assert loaded.value is not None
    assert "COULD NOT BE BACKED UP" in loaded.notice
    assert not loaded.writable
    assert path.read_bytes() == original


def test_an_existing_backup_is_never_written_over(tmp_path: Path) -> None:
    """The one thing a backup exists to prevent is a backup being overwritten.

    A save directory two installations have taken turns with, or a file restored from
    elsewhere, brings a second document to a version that has already been backed up.
    Its backup takes the next ordinal; the first one is still byte-for-byte what it was,
    even though the file it came from is gone.
    """
    path = tmp_path / "settings.json"
    first = _write(path, {"frame_cap": 90, "scale": 2})
    store = _store(tmp_path, migrations=UPGRADE)
    store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)

    backup = tmp_path / f"settings.json.v{CURRENT}.bak"
    assert backup.read_bytes() == first

    second = _write(path, {"frame_cap": 30, "scale": 5})
    assert second != first
    loaded = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)

    assert loaded.value == LocalSettings(scale=5, frame_cap=30, display_name="player")
    assert backup.read_bytes() == first
    assert (tmp_path / f"settings.json.v{CURRENT}.bak.2").read_bytes() == second


def test_backups_accumulate_until_the_limit_and_then_block_the_upgrade(
    tmp_path: Path,
) -> None:
    """Full is full: the upgrade stops rather than freeing a slot by deleting one."""
    path = tmp_path / "settings.json"
    store = _store(tmp_path, migrations=UPGRADE)
    originals: list[bytes] = []
    for cap in range(30, 30 + store_module.MAX_BACKUPS):
        originals.append(_write(path, {"frame_cap": cap, "scale": 2}))
        loaded = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)
        assert loaded.writable, cap

    last = _write(path, {"frame_cap": 120, "scale": 2})
    blocked = store.load(SETTINGS_KIND, current_version=NEXT, parse=settings_from_data)

    assert blocked.value is not None
    assert "TOO MANY BACKUPS" in blocked.notice
    assert not blocked.writable
    assert path.read_bytes() == last

    names = sorted(file.name for file in tmp_path.glob("settings.json.v*.bak*"))
    assert len(names) == store_module.MAX_BACKUPS
    assert (tmp_path / f"settings.json.v{CURRENT}.bak").read_bytes() == originals[0]


def test_a_backup_that_fails_part_way_leaves_nothing_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A half-written backup must never be mistaken for a copy of anything.

    Driven at the writer rather than through a load, because the failure being described
    is the one that happens *after* the file exists: the name is claimed, the bytes are
    not all there, and what has to be true is that the name is given back.
    """

    def refuse(descriptor: int) -> None:
        raise OSError("the disk filled up")

    monkeypatch.setattr(os, "fsync", refuse)
    target = tmp_path / f"settings.json.v{CURRENT}.bak"
    with pytest.raises(OSError):
        store_module._write_new_file(target, b"half a backup")
    assert not target.exists()


def test_a_backup_claims_its_name_exclusively(tmp_path: Path) -> None:
    """The check for a free name and the claim on it are one operation."""
    target = tmp_path / "taken.bak"
    target.write_bytes(b"somebody else's copy")
    with pytest.raises(FileExistsError):
        store_module._write_new_file(target, b"mine")
    assert target.read_bytes() == b"somebody else's copy"
