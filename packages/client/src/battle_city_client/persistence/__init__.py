"""Local, versioned settings and campaign progress, with a recovery path for every failure.

This package is the client's whole persistence story. It is pure Python over the standard
library and the protocol's hardened JSON, it imports no display library, and nothing in it
is reachable from the simulation, the campaign rules or any message that goes on a wire.

The format
----------
Two files, in one directory, each a single self-describing JSON object::

    settings.json   {"data": {...}, "format": "battle-city-reimagined/local-save",
                     "kind": "settings", "schema_version": 1}
    campaign.json   {"data": {...}, "format": "battle-city-reimagined/local-save",
                     "kind": "campaign", "schema_version": 1}

``settings`` holds the window scale, the frame cap and the roster name -- the three
preferences this build has -- and nothing else. No server address, no session identifier
and no lobby ticket is written anywhere: those belong to one launch and stay on the command
line, so a settings file can never be a place a credential ends up.

``campaign`` holds a stage-boundary checkpoint, three forward-only counters (stages
cleared, campaigns completed, best score) and the identifier of the selected cosmetic
badge. A checkpoint is the stage a campaign stood at, the score and lives that stage
*opened* on -- the anchors the campaign already keeps for restarting a stage -- the
campaign seed, and a digest identifying the stage's content. The seed is there because
every random stream a stage draws from is derived from it, so a campaign resumed under a
different one is a different campaign wearing the same score; it is stored folded into the
64 bits the generator uses, which is the position the derivation starts from either way.
The digest is there because a level identifier names a stage and does not identify one:
the same name lives in another pack, and a pack is edited in place.

A checkpoint is not a snapshot of a live simulation: there is no tick, no tank, no terrain
damage and no generator position in a save, because a mid-stage save would be a second
source of truth for simulation state and the canonical encoding that would have to version
it is reserved to an accepted proposal.

Where the files live
--------------------
``$BATTLE_CITY_SAVE_DIR``, else ``$XDG_DATA_HOME/battle-city-reimagined``, else
``%APPDATA%\\battle-city-reimagined``, else ``~/.local/share/battle-city-reimagined``. The
path is resolved on first use, never at import, and the directory is created only by a
write.

Writing
-------
Every write is a temporary file, flushed and ``fsync``-ed, then moved onto the target with
:func:`os.replace`, then a ``fsync`` of the directory. A reader sees the whole old file or
the whole new one. The temporary is exclusive and uniquely named, so two writers never
stage into the same file, and a failed write removes only its own. A temporary left behind
by a kill is inert and is left alone, since nothing here can tell it from one another
writer is still filling. Nothing is written unless something actually changed.

Reading is bounded before anything is parsed: at most one byte past the size limit is read
from the file, so an oversized one is refused without being loaded.

Recovery, and its limits
------------------------
A file that cannot be read never stops a launch. Defaults are used in memory, a short
notice is shown on the main menu and the options screen, and **the file on disk is left
exactly as it is for the rest of the session** -- for a corrupt file because it is the only
copy of whatever it was, and for a file from a newer build because it is not damaged at
all and overwriting it would discard what that build recorded. An older file is migrated,
and the original is copied to ``<name>.v<version>.bak`` before the upgraded document
replaces it; if that backup cannot be written the upgrade is abandoned rather than
performed without a way back. An existing backup is never written over: later ones take
``.bak.2`` through ``.bak.9``, each created exclusively, and when every ordinal is taken
the upgrade is refused rather than performed over somebody's recovery copy.

A save that names content this installation does not have is a recovery case of its own
and is treated the same way: the run is refused, the reason is shown, and **the save is
kept**. A pack that is swapped back makes it usable again, so a content directory that
changed is never quietly destructive.

What recovery does *not* do, said plainly:

* It does not repair a damaged file. There is no partial read and no salvage: a document
  is valid as a whole or it is not used at all.
* It does not keep a rolling history of ordinary saves. Backups are made by migrations
  alone, named for the version they came from.
* It does not clear old backups. Nine at one version is the limit, and reaching it blocks
  the upgrade until somebody looks at the directory.
* It does not restore a file a player deleted, and it cannot tell a deleted file from a
  first launch -- both are simply "no file yet", and neither is reported as a failure.
* It does not protect against a disk that lies about ``fsync``.

Layout
------
``documents``   the envelope, the bounded strict decoder, and the field readers
``migrations``  pure version-to-version steps, and the walk between two versions
``settings``    the settings record and its strict reader
``progress``    the campaign record, the stage checkpoint, and their strict readers
``cosmetics``   badges, and the unlock rules derived from the campaign record
``store``       where files live, atomic replacement, backups, and failure handling
``profile``     the in-memory profile the rest of the client talks to
"""

from .cosmetics import BADGES, Badge, cycled_badge, selected_badge, unlocked_badges
from .documents import (
    FORMAT_TAG,
    MAX_DOCUMENT_BYTES,
    CorruptSave,
    Document,
    FutureSchema,
    SaveError,
    decode_document,
    encode_document,
)
from .migrations import (
    NO_MIGRATIONS,
    MigrationFailed,
    MigrationRegistry,
    MigrationStep,
    MigrationTable,
    SavePayload,
    migrate,
)
from .profile import LocalProfile
from .progress import (
    DEFAULT_BADGE_ID,
    MAX_SEED,
    PROGRESS_KIND,
    PROGRESS_SCHEMA_VERSION,
    CampaignProgress,
    StageCheckpoint,
    checkpoint_from_data,
    progress_from_data,
)
from .settings import (
    DEFAULT_DISPLAY_NAME,
    DEFAULT_FRAME_CAP,
    MAX_FRAME_CAP,
    MIN_FRAME_CAP,
    SETTINGS_KIND,
    SETTINGS_SCHEMA_VERSION,
    LocalSettings,
    settings_from_data,
)
from .store import (
    APPLICATION_DIRECTORY,
    MIGRATIONS,
    SAVE_DIR_ENV,
    Loaded,
    ProfileStore,
    default_save_dir,
)

__all__ = [
    "APPLICATION_DIRECTORY",
    "BADGES",
    "DEFAULT_BADGE_ID",
    "DEFAULT_DISPLAY_NAME",
    "DEFAULT_FRAME_CAP",
    "FORMAT_TAG",
    "MAX_DOCUMENT_BYTES",
    "MAX_FRAME_CAP",
    "MAX_SEED",
    "MIGRATIONS",
    "MIN_FRAME_CAP",
    "NO_MIGRATIONS",
    "PROGRESS_KIND",
    "PROGRESS_SCHEMA_VERSION",
    "SAVE_DIR_ENV",
    "SETTINGS_KIND",
    "SETTINGS_SCHEMA_VERSION",
    "Badge",
    "CampaignProgress",
    "CorruptSave",
    "Document",
    "FutureSchema",
    "Loaded",
    "LocalProfile",
    "LocalSettings",
    "MigrationFailed",
    "MigrationRegistry",
    "MigrationStep",
    "MigrationTable",
    "ProfileStore",
    "SaveError",
    "SavePayload",
    "StageCheckpoint",
    "checkpoint_from_data",
    "cycled_badge",
    "decode_document",
    "default_save_dir",
    "encode_document",
    "migrate",
    "progress_from_data",
    "selected_badge",
    "settings_from_data",
    "unlocked_badges",
]
