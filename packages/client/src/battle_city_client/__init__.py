"""Pygame-CE presentation and input for Battle City Reimagined.

Launch it
---------
From the repository root::

    uv run --locked --package battle-city-client python -m battle_city_client

``--seed``, ``--scale`` and ``--frame-cap`` are accepted; ``--help`` lists them. The
client opens on a main menu, offers the three bundled classic stages, and plays the one
you pick. Arrows or WASD drive, space or ``J`` fires, ``ESC`` or ``P`` pauses, ``-`` and
``+`` change the window scale, and the window can be resized freely. There is no audio
and no gamepad support in this build.

Two small files are kept, under ``$BATTLE_CITY_SAVE_DIR`` or the platform's data
directory: the window scale, frame cap and roster name, and a campaign record holding a
stage-boundary checkpoint, a progression tally and the chosen cosmetic badge. They are
written only when something changes, through an atomic replace, and a file that cannot be
read leaves the client on its defaults with a notice on screen rather than failing a
launch. Nothing else touches a disk: a shell built without a store, which is every one but
a real launch's, keeps all of it in memory. See :mod:`battle_city_client.persistence`.

The client never needs a real display to be exercised. With the dummy SDL drivers the
whole loop runs headless, which is how the tests drive it::

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy uv run --locked pytest tests/client

Importing this package does not import pygame. The names backed by it are resolved on
first access; see :data:`_LAZY_EXPORTS`. ``README.md`` beside this file records why, how
the screen captures are refreshed, and one limitation of the simulation's import-purity
test that this package cannot fix from inside its own allowed files.

What this build is
------------------
A playable single-player campaign. Movement, firing, terrain damage, brick decay,
projectile reflection and the base are the simulation's, driven by real keyboard input at
a fixed tick rate. Enemy quotas, spawn cadence, score, lives across stages, stage clear,
campaign victory and the restart policy are :mod:`battle_city_client.campaign`'s, as
accepted in ``openspec/changes/campaign-v1`` and recorded in the product specification.

One thing is deliberately missing and is said plainly rather than implied: **the enemies
this build spawns hold position and never fire.** Steering belongs to ``battle_city_ai``,
and the architecture specification allows ``client -> sim, content, protocol`` and not
``client -> ai``, so the campaign takes an injected ``EnemyCommandDriver`` and the client
injects the one that commands nobody. ``tests/campaign`` injects a driver backed by the AI
package to show the seam carries a real bot; giving the shipped client one is issue #35.

There is also an online path, and it is a different game to the campaign rather than the
campaign over a wire. A client joins a server-owned lobby, agrees to the settings it is
shown, and plays a co-op match the server runs: it steps no simulation, keeps no local
copy, and draws the last snapshot it was sent. The campaign's waves, score and lives are
the single-player campaign's and are not yet run by a server, so an online match is the
shared simulation on an agreed stage and nothing more; see ``online.py`` and the
networking specification.

Failure is still only ever the simulation's: the client reads
``SimulationState.outcome`` and never sets one, and the campaign's ``FAILED`` phase is
derived from that same field. A base destroyed by a player's own shot is absorbed like
stone, so with enemies that do not fire a run ends by running out of lives only if the
player drives into something that can kill them -- which, in this build, nothing can. The
terminal presentation is therefore still exercised by tests that assemble a finished state
themselves, and those captures are labelled as fixtures.

Layout
------
``stage_adapter``  pure, validated ``Level`` to ``Stage`` mapping (no pygame, no clock)
``campaign``       the single-player campaign: waves, score, lives, win and loss
``timing``         elapsed milliseconds to whole ticks, in exact integers
``intents``        device-independent actions and the intent one tick is built from
``session``        one run: intent becomes legal simulation commands, nothing more
``persistence``    versioned local settings and campaign progress, and their recovery
``online``         one hosted match from the client's side: lobby, handover, remote run
``remote``         a server snapshot read into something the renderer can draw
``spectator``      a read-only window onto a session: frames in, nothing out
``netlink``        the transport the online session is pumped through (no pygame)
``shell``          the screen state machine, testable without a window
``keymap``         keycode tables, split by screen
``theme``          palette and the fixed logical frame
``glyphs``         a 5x7 bitmap font, stated as data
``assets``         the drawing seam: an ``AssetLibrary`` protocol plus stand-in art
``rendering``      blits a shell onto the logical frame
``display``        the window, and the whole-number scale between it and the frame
``app``            the pygame loop, and the only module that touches a device
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any, Final

from .campaign import (
    DEFAULT_CAMPAIGN_RULES,
    CampaignPhase,
    CampaignRules,
    CampaignRun,
    EnemyCommandDriver,
    IdleEnemyDriver,
    StagePlan,
    campaign_plan,
)
from .intents import Action, HeldActions, PlayerIntent, intent_from_held
from .netlink import NetworkLink, TcpLink, open_tcp_link, parse_endpoint
from .online import LobbyView, OnlineConfig, OnlinePhase, OnlineSession
from .persistence import (
    BADGES,
    Badge,
    CampaignProgress,
    LocalProfile,
    LocalSettings,
    ProfileStore,
    ReplayLibrary,
    StageCheckpoint,
)
from .remote import RemoteBoard, RemoteStateError, board_from_snapshot, terrain_from_rows
from .session import DEFAULT_SEED, StageSession
from .shell import ClientShell, PauseCause, Screen
from .spectator import SpectatorView
from .stage_adapter import (
    StageAdapterError,
    StageEntry,
    bundled_content_ref,
    bundled_stage_catalog,
    content_ref_for,
    stage_catalog,
    stage_from_level,
    stage_identity,
)
from .timing import NOMINAL_TICK_RATE, FixedTickAccumulator

if TYPE_CHECKING:
    from .app import ClientApp, build_app, main
    from .assets import AssetLibrary, ProceduralAssetLibrary
    from .display import Presenter, integer_scale, present_rect
    from .rendering import Renderer

_LAZY_EXPORTS: Final[dict[str, str]] = {
    "AssetLibrary": ".assets",
    "ClientApp": ".app",
    "Presenter": ".display",
    "ProceduralAssetLibrary": ".assets",
    "Renderer": ".rendering",
    "build_app": ".app",
    "integer_scale": ".display",
    "main": ".app",
    "present_rect": ".display",
}
"""Names whose module imports pygame, resolved on first use rather than on import.

Importing the client package must not import pygame. The repository's bootstrap contract
imports every workspace package in one interpreter, and the simulation's purity test
then asserts that no display library was pulled in; a package that reaches for pygame
merely to publish a name makes that assertion about the client rather than about the
simulation. It is also plain good manners: a tool that only wants
:func:`stage_from_level` or :class:`FixedTickAccumulator` should not pay for SDL.

The names below behave exactly as if they were imported here -- ``from
battle_city_client import Renderer`` works, and a type checker sees the real class
through the ``TYPE_CHECKING`` block above -- but the module behind each one is imported
on first access.
"""


def __getattr__(name: str) -> Any:
    """Resolve a pygame-backed export on first access. See :data:`_LAZY_EXPORTS`."""
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "Action",
    "AssetLibrary",
    "BADGES",
    "Badge",
    "CampaignPhase",
    "CampaignProgress",
    "CampaignRules",
    "CampaignRun",
    "ClientApp",
    "ClientShell",
    "DEFAULT_CAMPAIGN_RULES",
    "DEFAULT_SEED",
    "EnemyCommandDriver",
    "FixedTickAccumulator",
    "HeldActions",
    "IdleEnemyDriver",
    "LobbyView",
    "LocalProfile",
    "LocalSettings",
    "NOMINAL_TICK_RATE",
    "NetworkLink",
    "OnlineConfig",
    "OnlinePhase",
    "OnlineSession",
    "PauseCause",
    "PlayerIntent",
    "Presenter",
    "ProceduralAssetLibrary",
    "ProfileStore",
    "RemoteBoard",
    "RemoteStateError",
    "Renderer",
    "ReplayLibrary",
    "Screen",
    "SpectatorView",
    "StageAdapterError",
    "StageCheckpoint",
    "StageEntry",
    "StagePlan",
    "StageSession",
    "TcpLink",
    "board_from_snapshot",
    "build_app",
    "bundled_content_ref",
    "bundled_stage_catalog",
    "campaign_plan",
    "content_ref_for",
    "integer_scale",
    "intent_from_held",
    "main",
    "open_tcp_link",
    "parse_endpoint",
    "present_rect",
    "stage_catalog",
    "stage_from_level",
    "stage_identity",
    "terrain_from_rows",
]
