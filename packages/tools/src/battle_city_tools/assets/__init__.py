"""The sprite pipeline: Blender renders in, a validated atlas and its sidecar out.

Where the pieces live
---------------------
The art itself is authored in ``assets/blender/build_scene.py`` and rendered by
``assets/blender/render_frames.py``. Those two files are the only ones that import
``bpy``, and they are deliberately thin: a scene description and a render loop. Every
decision that can be made without Blender is made here instead, because ``assets/`` is
outside the repository's lint and type checking while this package is inside both.

What this package guarantees
----------------------------
*Nothing but the standard library and :mod:`battle_city_content`.* The validator has to
run in continuous integration on a machine with no GPU, no Blender and no image library,
so the PNG codec, the raster operations and the bitmap font are all local.

*The same renders always produce the same bytes.* Frames are sorted by name, packed by a
stateless shelf algorithm, boxed down with premultiplied coverage, snapped to binary alpha
and quantised to a declared palette. No step reads a clock, an environment variable or the
iteration order of a set.

*The sidecar can be checked, not just read.* Every claim it makes -- rectangles, pivots,
palette membership, duplicate frames, source identifiers, and the readability thresholds
that keep faction, mirror lean, cover and damage legible without colour -- is re-derived
from the pixels by :mod:`~battle_city_tools.assets.validation`.

*Art cannot move a hitbox.* Collision sizes are declared in
:mod:`~battle_city_tools.assets.catalog` and cross-checked against ``battle_city_sim`` by
the test suite. A sprite that overhangs its collision box says so with a pivot.

Using it
--------
::

    uv run --locked --package battle-city-tools python -m battle_city_tools.assets validate
    uv run --locked --package battle-city-tools python -m battle_city_tools.assets describe
    uv run --locked --package battle-city-tools python -m battle_city_tools.assets build \\
        --frames <render directory> --output assets/sprites/starter

Layout
------
``raster``      immutable RGBA images, downsampling, quantisation, compositing
``png``         a strict standard-library PNG reader and writer
``font``        a 5x7 alphabet, so generated pictures can label themselves
``palette``     the declared colours, shared with the client's temporary stand-ins
``catalog``     which frames exist, their pivots, their hitboxes, their readability rules
``layout``      the deterministic shelf packer
``metadata``    the versioned sidecar, its writer and its strict loader
``build``       renders in, tracked artifacts out
``preview``     the labelled contact sheet
``composite``   the labelled stage picture, which is not a game capture
``validation``  every claim in the sidecar, re-derived from the pixels
``cli``         ``python -m battle_city_tools.assets``
"""

from .build import BuildReport, build_pack
from .catalog import FRAME_NAMES, FRAME_SIZE, FRAMES, FRAMES_BY_NAME, HITBOX_SIZES, FrameSpec
from .errors import AssetDiagnostic, AssetError, AssetInvalid, AssetRefusal
from .layout import Layout, Placement, pack
from .metadata import ATLAS_FILENAME, METADATA_FILENAME, AtlasMetadata, load, serialize
from .palette import PALETTE, PALETTE_COLORS
from .png import decode_png, encode_png
from .raster import Canvas, Image
from .validation import ValidationReport, validate, validate_directory

__all__ = [
    "ATLAS_FILENAME",
    "FRAMES",
    "FRAMES_BY_NAME",
    "FRAME_NAMES",
    "FRAME_SIZE",
    "HITBOX_SIZES",
    "METADATA_FILENAME",
    "PALETTE",
    "PALETTE_COLORS",
    "AssetDiagnostic",
    "AssetError",
    "AssetInvalid",
    "AssetRefusal",
    "AtlasMetadata",
    "BuildReport",
    "Canvas",
    "FrameSpec",
    "Image",
    "Layout",
    "Placement",
    "ValidationReport",
    "build_pack",
    "decode_png",
    "encode_png",
    "load",
    "pack",
    "serialize",
    "validate",
    "validate_directory",
]
