"""Pygame-CE presentation and input for Battle City Reimagined.

Launch it
---------
From the repository root::

    uv run --locked --package battle-city-client python -m battle_city_client

``--seed``, ``--scale`` and ``--frame-cap`` are accepted; ``--help`` lists them. The
client opens on a main menu, offers the three bundled classic stages, and plays the one
you pick. Arrows or WASD drive, space or ``J`` fires, ``ESC`` or ``P`` pauses, ``-`` and
``+`` change the window scale, and the window can be resized freely. There is no audio
and no gamepad support in this build, and nothing is written to disk.

The client never needs a real display to be exercised. With the dummy SDL drivers the
whole loop runs headless, which is how the tests drive it::

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy uv run --locked pytest tests/client

What this build is
------------------
A playable client foundation. Movement, firing, terrain damage, brick decay, projectile
reflection and the base are the simulation's, driven by real keyboard input at a fixed
tick rate. What is *not* here is equally deliberate: no enemy behaviour, no wave
scheduler and no stage victory. The simulation ships none of those -- enemies are
first-class actors that only move when something commands them, and the product
specification defers wave pacing and win-state timing to an accepted gameplay proposal --
so the client shows the board honestly rather than inventing a rule in the presentation
layer. Enemy waves arrive with the AI phase and stage victory with the campaign phase.

Terminal screens are drawn for outcomes the simulation recorded, and the client never
records one. Since no live run can currently reach an outcome, the terminal presentation
is covered by tests that assemble a finished state themselves; those captures are labelled
as fixtures.

Layout
------
``stage_adapter``  pure, validated ``Level`` to ``Stage`` mapping (no pygame, no clock)
``timing``         elapsed milliseconds to whole ticks, in exact integers
``intents``        device-independent actions and the intent one tick is built from
``session``        one run: intent becomes legal simulation commands, nothing more
``shell``          the screen state machine, testable without a window
``keymap``         keycode tables, split by screen
``theme``          palette and the fixed logical frame
``glyphs``         a 5x7 bitmap font, stated as data
``assets``         the drawing seam: an ``AssetLibrary`` protocol plus stand-in art
``rendering``      blits a shell onto the logical frame
``display``        the window, and the whole-number scale between it and the frame
``app``            the pygame loop, and the only module that touches a device
"""

from .app import ClientApp, build_app, main
from .assets import AssetLibrary, ProceduralAssetLibrary
from .display import Presenter, integer_scale, present_rect
from .intents import Action, HeldActions, PlayerIntent, intent_from_held
from .rendering import Renderer
from .session import DEFAULT_SEED, StageSession
from .shell import ClientShell, PauseCause, Screen
from .stage_adapter import (
    StageAdapterError,
    StageEntry,
    bundled_stage_catalog,
    stage_catalog,
    stage_from_level,
)
from .timing import NOMINAL_TICK_RATE, FixedTickAccumulator

__all__ = [
    "DEFAULT_SEED",
    "NOMINAL_TICK_RATE",
    "Action",
    "AssetLibrary",
    "ClientApp",
    "ClientShell",
    "FixedTickAccumulator",
    "HeldActions",
    "PauseCause",
    "PlayerIntent",
    "Presenter",
    "ProceduralAssetLibrary",
    "Renderer",
    "Screen",
    "StageAdapterError",
    "StageEntry",
    "StageSession",
    "build_app",
    "bundled_stage_catalog",
    "integer_scale",
    "intent_from_held",
    "main",
    "present_rect",
    "stage_catalog",
    "stage_from_level",
]
