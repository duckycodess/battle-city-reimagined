"""What the starter pack contains: the frames, their anchors, and what must stay legible.

This module is the one place that says which sprites exist. The Blender scripts render a
plan that a test checks against this catalogue, the packer lays these names out, the
metadata writer copies these records, and the validator re-derives every claim from them.
Adding a sprite means adding a line here and a cell in the scene; nothing else guesses.

Three things are deliberately *declared* rather than measured.

**Hitboxes.** The art pipeline specification says pivots and hitboxes are separate
metadata and that art cannot change collision geometry. The sizes below are therefore
written out literally, and ``tests/assets`` asserts they still equal
``battle_city_sim.DEFAULT_RULES``. A sprite that grows past its hitbox is a pivot change,
never a hitbox change; the asset tools do not import the simulation, so the only way the
two can disagree is a failing test.

**Pivots.** A pivot is where the frame's top left sits relative to the entity's own
anchor, in frame pixels. A tank's position is its top-left pixel, so tank art anchors at
``(0, 0)``; a projectile's position is its centre pixel, so a shot anchors at the middle
of its frame. Changing a pivot moves art. It never moves a collision box.

**Readability.** The accessibility and art specifications require a *property*: faction,
mirror direction, forest cover, water animation, damage and invincibility must stay
readable without relying on colour alone. They state no numbers, and the numbers below are
not derived from them.

What the numbers are is this pack's own operational thresholds -- regression guards, set
by measuring the shipped art and then writing down a value comfortably underneath it, so
that a redraw which quietly flattens a distinction fails a test instead of shipping. Every
rule currently passes with margin; the tightest is the player-versus-enemy mean-luma gap,
where the art measures 25 against a threshold of 18.

Three things follow from that, and are worth being plain about. Passing these thresholds
is evidence the property holds, not proof of it, and it is not a conformance claim against
any accessibility standard -- there is no contrast ratio here and no stated viewing
condition. The thresholds are calibrated to *this* art, so raising one is an art decision
recorded in this file, and lowering one is an admission that a distinction got weaker,
which is exactly the conversation the number exists to force. And the set of pairs is a
judgement about which distinctions a player must make, not an exhaustive derivation from
the specifications; a mechanical check over grey-scale pixels is a floor under the
property, never a substitute for looking at the art.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

FRAME_SIZE: Final[tuple[int, int]] = (16, 16)
"""Every frame is one tile square. Smaller art is centred inside a frame of this size.

A single frame size keeps one orthographic camera, one ``ortho_scale`` and one
pixels-per-unit for the whole pack, which is most of what makes the render reproducible.
A three-pixel projectile in a sixteen-pixel frame costs transparent bytes and buys a
pipeline with no special cases.
"""

HITBOX_SIZES: Final[dict[str, tuple[int, int] | None]] = {
    "tile": (16, 16),
    "tank": (16, 16),
    "projectile": (3, 3),
    "none": None,
}
"""Collision geometry by kind, owned by the simulation and mirrored here.

``tile`` is ``rules.tile_size`` square, ``tank`` is ``rules.tank_size`` square and
``projectile`` is ``2 * rules.projectile_radius + 1`` square. ``none`` marks art with no
collision geometry at all: effects exist only on screen.
"""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Where a group of frames came from, and what is claimed about the rights in it."""

    source_id: str
    scene: str
    build_script: str
    render_script: str
    collection: str
    authors: tuple[str, ...]
    spdx_id: str
    notice: str


@dataclass(frozen=True, slots=True)
class FrameSpec:
    """One sprite: what it is, where it anchors, and which collision box it depicts."""

    name: str
    group: str
    source_id: str
    hitbox: str
    pivot: tuple[int, int]
    description: str


@dataclass(frozen=True, slots=True)
class AnimationSpec:
    """A named frame sequence. Durations are deliberately absent.

    No accepted specification sets effect or water timing, and inventing one here would
    quietly create a gameplay contract out of an art asset. The sequence and its order are
    art; how long a frame is held belongs to a change proposal.
    """

    name: str
    frames: tuple[str, ...]
    loop: bool


AUTHORS: Final[tuple[str, ...]] = ("Battle City Reimagined contributors",)
LICENSE_SPDX: Final[str] = "NOASSERTION"
LICENSE_NOTICE: Final[str] = (
    "Every mesh, material and light in these frames was authored by the build script in "
    "this repository and rendered by its render script. No artwork, texture or sprite "
    "sheet from the historical duckycodess/Battle-City repository was copied, traced or "
    "re-encoded, and no generated image is a runtime dependency. The repository publishes "
    "no licence file, so this pack asserts none on its owner's behalf; NOASSERTION is the "
    "SPDX value for exactly that, matching the bundled content pack."
)

SCENE: Final[str] = "assets/blender/starter_set.blend"
BUILD_SCRIPT: Final[str] = "assets/blender/build_scene.py"
RENDER_SCRIPT: Final[str] = "assets/blender/render_frames.py"


def _source(source_id: str, collection: str) -> SourceSpec:
    return SourceSpec(
        source_id=source_id,
        scene=SCENE,
        build_script=BUILD_SCRIPT,
        render_script=RENDER_SCRIPT,
        collection=collection,
        authors=AUTHORS,
        spdx_id=LICENSE_SPDX,
        notice=LICENSE_NOTICE,
    )


SOURCES: Final[tuple[SourceSpec, ...]] = (
    _source("starter-set/effects", "effects"),
    _source("starter-set/powerups", "powerups"),
    _source("starter-set/projectiles", "projectiles"),
    _source("starter-set/tanks", "tanks"),
    _source("starter-set/terrain", "terrain"),
)

_TILE_PIVOT: Final[tuple[int, int]] = (0, 0)
_CENTRE_PIVOT: Final[tuple[int, int]] = (8, 8)

_TERRAIN: Final[tuple[tuple[str, str], ...]] = (
    ("terrain-brick", "Brick wall, tile 2: destroyed by one hit."),
    ("terrain-brick-cracked", "Cracked brick, tile 6: brick already damaged once."),
    ("terrain-empty", "Open ground, tile 0: the only surface tanks and shots share."),
    ("terrain-forest", "Forest canopy, tile 7: drawn over tanks, which it conceals."),
    ("terrain-home-destroyed", "Home base, tile 8, after a hostile shot reached it."),
    ("terrain-home-intact", "Home base, tile 8: the thing the run is about."),
    ("terrain-mirror-ne", "Mirror tile 3: a backslash surface that sends a shot right-to-down."),
    ("terrain-mirror-se", "Mirror tile 4: a slash surface that sends a shot right-to-up."),
    ("terrain-stone", "Stone wall, tile 1: stops shots and is never destroyed."),
    ("terrain-water-0", "Water, tile 5, crest phase 0: blocks tanks, passes shots."),
    ("terrain-water-1", "Water, tile 5, crest phase 1."),
    ("terrain-water-2", "Water, tile 5, crest phase 2."),
)

_TANK_VARIANTS: Final[tuple[tuple[str, str], ...]] = (
    ("player", "Player tank: blue hull with a forward chevron."),
    ("enemy-normal", "Enemy tank, variant 1: red hull with a single turret eye."),
    ("enemy-shielded", "Enemy tank, variant 2: bolted armour plates on every face."),
    ("enemy-unshielded", "Enemy tank, variant 3: armour stripped, hull frame exposed."),
)

_DIRECTIONS: Final[tuple[str, ...]] = ("up", "down", "left", "right")

_POWERUPS: Final[tuple[tuple[str, str], ...]] = (
    ("powerup-extra-life", "Extra life pickup: a thick cross on a framed plate."),
    ("powerup-gatling", "Gatling pickup: three stacked barrels on a framed plate."),
    ("powerup-invincibility", "Invincibility pickup: a shield boss on a framed plate."),
)

_EFFECTS: Final[tuple[tuple[str, str], ...]] = (
    ("effect-explosion-0", "Explosion, frame 0: a tight core."),
    ("effect-explosion-1", "Explosion, frame 1: the core breaks into spokes."),
    ("effect-explosion-2", "Explosion, frame 2: spokes at full reach."),
    ("effect-explosion-3", "Explosion, frame 3: scattered embers."),
    ("effect-shield-0", "Invincibility ring, phase 0: six segments on the axes."),
    ("effect-shield-1", "Invincibility ring, phase 1: the same ring, rotated."),
    ("effect-spawn-0", "Spawn mark, frame 0: four wedges far out."),
    ("effect-spawn-1", "Spawn mark, frame 1: the wedges closing in."),
    ("effect-spawn-2", "Spawn mark, frame 2: the wedges meeting at the centre."),
)


def _build_frames() -> tuple[FrameSpec, ...]:
    frames: list[FrameSpec] = []
    for name, description in _TERRAIN:
        frames.append(
            FrameSpec(
                name=name,
                group="terrain",
                source_id="starter-set/terrain",
                hitbox="tile",
                pivot=_TILE_PIVOT,
                description=description,
            )
        )
    for variant, description in _TANK_VARIANTS:
        for direction in _DIRECTIONS:
            frames.append(
                FrameSpec(
                    name=f"tank-{variant}-{direction}",
                    group="tank",
                    source_id="starter-set/tanks",
                    hitbox="tank",
                    pivot=_TILE_PIVOT,
                    description=f"{description} Facing {direction}.",
                )
            )
    for direction in _DIRECTIONS:
        frames.append(
            FrameSpec(
                name=f"shot-{direction}",
                group="projectile",
                source_id="starter-set/projectiles",
                hitbox="projectile",
                pivot=_CENTRE_PIVOT,
                description=f"Projectile travelling {direction}, drawn around its centre pixel.",
            )
        )
    for name, description in _POWERUPS:
        frames.append(
            FrameSpec(
                name=name,
                group="powerup",
                source_id="starter-set/powerups",
                hitbox="tile",
                pivot=_TILE_PIVOT,
                description=description,
            )
        )
    for name, description in _EFFECTS:
        frames.append(
            FrameSpec(
                name=name,
                group="effect",
                source_id="starter-set/effects",
                hitbox="none",
                pivot=_CENTRE_PIVOT,
                description=description,
            )
        )
    return tuple(sorted(frames, key=lambda frame: frame.name))


FRAMES: Final[tuple[FrameSpec, ...]] = _build_frames()
FRAMES_BY_NAME: Final[dict[str, FrameSpec]] = {frame.name: frame for frame in FRAMES}
FRAME_NAMES: Final[tuple[str, ...]] = tuple(frame.name for frame in FRAMES)

ANIMATIONS: Final[tuple[AnimationSpec, ...]] = (
    AnimationSpec(
        name="explosion",
        frames=(
            "effect-explosion-0",
            "effect-explosion-1",
            "effect-explosion-2",
            "effect-explosion-3",
        ),
        loop=False,
    ),
    AnimationSpec(name="shield", frames=("effect-shield-0", "effect-shield-1"), loop=True),
    AnimationSpec(
        name="spawn",
        frames=("effect-spawn-0", "effect-spawn-1", "effect-spawn-2"),
        loop=False,
    ),
    AnimationSpec(
        name="water",
        frames=("terrain-water-0", "terrain-water-1", "terrain-water-2"),
        loop=True,
    ),
)

STATE_FRAMES: Final[tuple[tuple[str, str], ...]] = (
    ("home.destroyed", "terrain-home-destroyed"),
    ("home.intact", "terrain-home-intact"),
    ("tile.2.damaged", "terrain-brick-cracked"),
    ("tile.2.intact", "terrain-brick"),
)
"""Entity states the client cannot infer from a tile code alone, mapped to a frame.

The base is one tile code with two looks, and brick damage is two tile codes that are one
wall to a player. Spelling the mapping out here means the eventual asset library reads a
table instead of hard-coding a pair of names.
"""

LUMA_CONTRAST: Final[tuple[tuple[str, str, int], ...]] = (
    ("tank-player-up", "tank-enemy-normal-up", 18),
    ("tank-player-left", "tank-enemy-normal-left", 18),
    ("terrain-forest", "terrain-empty", 30),
    ("terrain-home-intact", "terrain-home-destroyed", 12),
    ("terrain-stone", "terrain-brick", 12),
)
"""Pairs whose mean luma over the frame differs by at least the given amount in this pack.

Mean luma is taken over the whole frame with transparent pixels counted as black, which
is what a player sees against the playfield. These are the pairs a viewer must still tell
apart with the colour removed: the two factions, cover versus open ground, a base that is
still standing, and the two wall materials.

The amounts are measured floors under the shipped art, not minima any specification sets.
At the time of writing the pairs measure 25, 26, 58, 42 and 38 against the 18, 18, 30, 12
and 12 recorded here.
"""

SILHOUETTE_DISTINCT: Final[tuple[tuple[str, str, int], ...]] = (
    ("tank-enemy-shielded-up", "tank-enemy-unshielded-up", 40),
    ("powerup-extra-life", "powerup-gatling", 40),
    ("powerup-extra-life", "powerup-invincibility", 40),
    ("powerup-gatling", "powerup-invincibility", 40),
    ("terrain-forest", "terrain-empty", 60),
    ("effect-explosion-0", "effect-explosion-2", 60),
    ("effect-spawn-0", "effect-spawn-2", 60),
)
"""Pairs whose opaque silhouettes differ on at least this many pixels per thousand.

Shape, not hue. A shielded and an unshielded enemy are the same red; the armour has to
change the outline. The three powerups sit on the same plate; the icon has to change it.

Again a floor under what the art does rather than a mandated minimum. The tightest pair
here is forest against open ground at 70 per thousand over a floor of 60; the loosest is
the two explosion phases at 453.
"""

LUMA_DISTINCT: Final[tuple[tuple[str, str, int], ...]] = (
    ("terrain-mirror-ne", "terrain-mirror-se", 120),
    ("terrain-water-0", "terrain-water-1", 60),
    ("terrain-water-1", "terrain-water-2", 60),
    ("terrain-water-0", "terrain-water-2", 60),
    ("tank-player-up", "tank-player-down", 80),
    ("tank-player-left", "tank-player-right", 80),
    ("shot-up", "shot-down", 60),
    ("shot-left", "shot-right", 60),
    ("effect-shield-0", "effect-shield-1", 40),
)
"""Pairs that differ in grey scale on at least this many pixels per thousand.

A pixel counts when its luma differs by at least :data:`LUMA_DISTINCT_STEP`. The two
mirrors fill the same square, so no silhouette test can separate them; water changes shape
between phases rather than only hue; a facing is readable from the hull.

These are the pack's floors, not specification minima. The tightest are the two shot axes
at 78 per thousand over a floor of 60.
"""

LUMA_DISTINCT_STEP: Final[int] = 24
"""How far two luma values differ before this pack counts the pixel as visibly different.

A chosen working value, not a perceptual constant. Roughly a tenth of the range, which is
coarse enough that palette neighbours and anti-aliased edges do not register as a
difference and fine enough that a deliberate tonal change does.
"""

QUADRANT_SIGN: Final[tuple[tuple[str, int, int], ...]] = (
    ("terrain-mirror-ne", -1, 10),
    ("terrain-mirror-se", 1, 10),
)
"""Which way each mirror's bright diagonal leans, as a signed luma difference.

The metric is the mean luma of the top-right quarter minus the mean luma of the top-left
quarter. A ``\\`` surface is bright at the top left, so its metric is negative; a ``/``
surface is bright at the top right, so its metric is positive.

Here the *sign* is the real assertion and is not a calibrated threshold at all: it is the
deflection the simulation implements, so getting it backwards is a correctness bug rather
than a readability regression. The magnitude of 10 is only a guard against a frame so flat
that its sign is noise; the art measures -73 and +68.

The signs follow the deflection table, not the tile names. ``battle_city_sim.tiles``
records that the historical names read backwards against their geometry: ``MIRROR_NE``
sends a rightward shot downward, which is a ``\\`` surface, and ``MIRROR_SE`` sends it
upward, which is a ``/``. Art that followed the names would teach a player to predict the
wrong bounce, so it follows the geometry; renaming the tiles would need a content proposal
and is not this pack's call to make.
"""
