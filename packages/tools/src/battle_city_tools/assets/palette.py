"""The declared palette every shipped frame is quantised into.

The hues are the client's own, from ``battle_city_client.theme``. That module draws the
temporary procedural stand-ins, and the art pipeline specification says the shipped art
replaces them; a palette agreeing with the stand-ins is what keeps the swap a change of
asset library rather than a change of look. The duplicated values are deliberate and not
an import: ``battle_city_tools`` may not depend on the client package, and a cross-package
test is the right place to notice a drift, not a runtime import that would make the asset
tools need a display library.

The shade entries that have no counterpart in the theme are the pipeline's own. A render
is lit geometry, so it arrives with a continuous ramp between a lit face and a shadowed
one; without mid tones the quantiser would snap a whole shaded face to one flat colour
and throw away the silhouette the lighting was there to produce.
"""

from __future__ import annotations

from typing import Final

from .raster import Rgb

PALETTE: Final[tuple[tuple[str, Rgb], ...]] = (
    ("void", (10, 12, 18)),
    ("ground", (18, 20, 27)),
    ("ground-speck", (26, 29, 38)),
    ("panel", (24, 28, 38)),
    ("panel-edge", (58, 68, 88)),
    ("tread", (32, 36, 46)),
    ("stone-shadow", (66, 72, 86)),
    ("stone-dark", (104, 112, 128)),
    ("stone-mid", (130, 139, 154)),
    ("stone", (158, 166, 180)),
    ("brick-mortar", (58, 34, 26)),
    ("brick-dark", (120, 62, 40)),
    ("brick-mid", (148, 79, 51)),
    ("brick", (176, 96, 62)),
    ("crack", (238, 226, 206)),
    ("water-deep", (26, 56, 96)),
    ("water", (44, 92, 152)),
    ("water-mid", (68, 122, 182)),
    ("water-crest", (96, 154, 214)),
    ("forest-dark", (24, 62, 36)),
    ("forest", (46, 106, 62)),
    ("forest-mid", (64, 136, 80)),
    ("forest-leaf", (84, 168, 100)),
    ("mirror-dark", (120, 132, 156)),
    ("mirror-mid", (164, 174, 196)),
    ("mirror", (206, 216, 234)),
    ("home-dark", (146, 118, 40)),
    ("home-mid", (192, 166, 80)),
    ("home", (238, 214, 120)),
    ("player-dark", (44, 118, 168)),
    ("player-mid", (74, 158, 208)),
    ("player", (108, 198, 244)),
    ("enemy-dark", (150, 54, 48)),
    ("enemy-mid", (192, 80, 72)),
    ("enemy", (236, 108, 96)),
    ("powerup-fill", (52, 44, 22)),
    ("powerup-frame", (246, 190, 64)),
    ("danger", (232, 84, 72)),
    ("ok", (124, 208, 132)),
    ("text-dim", (134, 146, 168)),
    ("text", (226, 232, 240)),
    ("shot-enemy", (252, 214, 150)),
    ("shot-player", (238, 244, 252)),
)
"""Named colours in a fixed order. The order is part of the contract.

:meth:`~battle_city_tools.assets.raster.Image.quantized` breaks a distance tie by taking
the earlier entry, so reordering this tuple can change checked-in pixels even though no
colour changed.
"""

PALETTE_COLORS: Final[tuple[Rgb, ...]] = tuple(color for _, color in PALETTE)
PALETTE_NAMES: Final[dict[Rgb, str]] = {color: name for name, color in PALETTE}
PALETTE_SET: Final[frozenset[Rgb]] = frozenset(PALETTE_COLORS)
