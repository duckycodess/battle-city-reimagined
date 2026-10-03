"""Build the starter sprite scene and save it as ``starter_set.blend``.

Run with Blender, never with the project interpreter::

    blender --background --factory-startup \
        --python assets/blender/build_scene.py -- --output assets/blender/starter_set.blend

Why a script and not a hand-modelled file
-----------------------------------------
The ``.blend`` is a convenience: it opens in Blender, it can be inspected, and it is what
the render command loads. It is not the source of truth, because a ``.blend`` embeds
build metadata and pointer layout and is therefore not byte-reproducible. *This file* is
the versioned source. Deleting the ``.blend`` and re-running this script reproduces the
scene; editing the ``.blend`` by hand and not editing this script is how the two drift.

How the scene is organised
--------------------------
Every sprite is one cell: a collection named ``cell/<id>`` holding an empty at the cell
origin with all of that sprite's geometry parented to it. Cells are laid out on a grid
with two units of spacing purely so a human can open the file and see them side by side;
the render command points the camera at one cell at a time and hides the rest, so a
neighbour can never leak a pixel or a bounce into a frame.

One world unit is one tile. The camera is orthographic with ``ortho_scale`` 1.0, so a cell
is exactly the 16-pixel frame the pack ships, rendered at a whole-number multiple and
box-averaged back down by the packer. Geometry is built from two primitives, a box and a
regular prism, both generated from vertex data rather than through ``bpy.ops``, so the
mesh depends on the numbers in this file and on nothing about the session.

Lighting is two suns and a dim world. The key comes from the upper left of the frame and
never moves; a tank facing a different way is the *object* rotating under a fixed light,
which is what keeps the highlight in the same place on every sprite in the pack. Colours
are the shipped palette, converted from sRGB to linear here because the render is written
back out through a Standard view transform.

Nothing here is traced, converted or copied from the historical repository.
"""

from __future__ import annotations

import argparse
import math
import sys

import bpy

# --------------------------------------------------------------------------------------
# Palette. These names and values are a subset of
# ``battle_city_tools.assets.palette.PALETTE``; ``tests/assets`` parses this file and
# fails if a colour here is not a shipped palette entry, because a material the quantiser
# has no entry for would be silently snapped to its nearest neighbour.
# --------------------------------------------------------------------------------------

PALETTE = {
    "void": (10, 12, 18),
    "ground": (18, 20, 27),
    "ground-speck": (26, 29, 38),
    "tread": (32, 36, 46),
    "stone-shadow": (66, 72, 86),
    "stone-dark": (104, 112, 128),
    "stone": (158, 166, 180),
    "brick-mortar": (58, 34, 26),
    "brick-dark": (120, 62, 40),
    "brick": (176, 96, 62),
    "crack": (238, 226, 206),
    "water-deep": (26, 56, 96),
    "water": (44, 92, 152),
    "water-crest": (96, 154, 214),
    "forest-dark": (24, 62, 36),
    "forest": (46, 106, 62),
    "forest-mid": (64, 136, 80),
    "forest-leaf": (84, 168, 100),
    "mirror-dark": (120, 132, 156),
    "mirror": (206, 216, 234),
    "home-dark": (146, 118, 40),
    "home": (238, 214, 120),
    "player-dark": (44, 118, 168),
    "player-mid": (74, 158, 208),
    "player": (108, 198, 244),
    "enemy-dark": (150, 54, 48),
    "enemy-mid": (192, 80, 72),
    "enemy": (236, 108, 96),
    "powerup-frame": (246, 190, 64),
    "danger": (232, 84, 72),
    "text-dim": (134, 146, 168),
    "shot-enemy": (252, 214, 150),
    "shot-player": (238, 244, 252),
}

EMISSIVE = ("crack", "shot-player", "shot-enemy", "powerup-frame", "danger")
"""Materials that light themselves. Shots, sparks and pickup frames must read at 16 px.

An emission shader is also how a tiny sprite keeps a flat, saturated colour: a three-pixel
bolt shaded by a sun would land on whatever ramp step the quantiser rounded it to.
"""

CELL_SPACING = 2.0
CELL_COLUMNS = 8

# --------------------------------------------------------------------------------------
# Cells. The render plan in ``render_frames.py`` names these; a test checks the two agree.
# --------------------------------------------------------------------------------------

CELLS = (
    "terrain-empty",
    "terrain-stone",
    "terrain-brick",
    "terrain-brick-cracked",
    "terrain-mirror-ne",
    "terrain-mirror-se",
    "terrain-water-0",
    "terrain-water-1",
    "terrain-water-2",
    "terrain-forest",
    "terrain-home-intact",
    "terrain-home-destroyed",
    "tank-player",
    "tank-enemy-normal",
    "tank-enemy-shielded",
    "tank-enemy-unshielded",
    "shot",
    "powerup-gatling",
    "powerup-invincibility",
    "powerup-extra-life",
    "effect-explosion-0",
    "effect-explosion-1",
    "effect-explosion-2",
    "effect-explosion-3",
    "effect-spawn-0",
    "effect-spawn-1",
    "effect-spawn-2",
    "effect-shield-0",
    "effect-shield-1",
)

# --------------------------------------------------------------------------------------
# Pinned render settings. ``render_frames.py`` reads these back out of the saved file and
# records what actually rendered, rather than trusting this list.
# --------------------------------------------------------------------------------------

SUPERSAMPLE = 4
FRAME_PIXELS = 16
RENDER_PIXELS = FRAME_PIXELS * SUPERSAMPLE
CYCLES_SAMPLES = 96
CYCLES_SEED = 0
MAX_BOUNCES = 1
KEY_DIRECTION = (0.45, -0.45, -0.77)
KEY_ENERGY = 2.9
FILL_DIRECTION = (-0.35, 0.3, -0.89)
FILL_ENERGY = 0.9
WORLD_COLOR = (0.16, 0.18, 0.24)
WORLD_STRENGTH = 0.35
CAMERA_HEIGHT = 6.0


def srgb_to_linear(channel):
    """Convert one 0..255 sRGB channel to the linear value Blender wants."""
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def linear_color(name):
    red, green, blue = PALETTE[name]
    return (srgb_to_linear(red), srgb_to_linear(green), srgb_to_linear(blue), 1.0)


def make_material(name):
    """A flat diffuse (or emissive) material, built from nodes so no default leaks in."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    if name in EMISSIVE:
        shader = tree.nodes.new("ShaderNodeEmission")
        shader.inputs["Color"].default_value = linear_color(name)
        shader.inputs["Strength"].default_value = 1.0
    else:
        shader = tree.nodes.new("ShaderNodeBsdfDiffuse")
        shader.inputs["Color"].default_value = linear_color(name)
        shader.inputs["Roughness"].default_value = 1.0
    tree.links.new(shader.outputs[0], output.inputs["Surface"])
    return material


MATERIALS = {}


def material(name):
    if name not in MATERIALS:
        MATERIALS[name] = make_material(name)
    return MATERIALS[name]


# --------------------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------------------


def add_mesh(cell, name, verts, faces, color, location, rot_z=0.0):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.materials.append(material(color))
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.rotation_euler = (0.0, 0.0, math.radians(rot_z))
    cell.collection.objects.link(obj)
    obj.parent = cell.pivot
    return obj


def box(cell, name, center, size, color, rot_z=0.0):
    """An axis-aligned box of ``size`` centred on ``center``, optionally spun about Z."""
    half_x, half_y, half_z = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    verts = [
        (-half_x, -half_y, -half_z),
        (half_x, -half_y, -half_z),
        (half_x, half_y, -half_z),
        (-half_x, half_y, -half_z),
        (-half_x, -half_y, half_z),
        (half_x, -half_y, half_z),
        (half_x, half_y, half_z),
        (-half_x, half_y, half_z),
    ]
    faces = [
        (0, 1, 2, 3),
        (7, 6, 5, 4),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    return add_mesh(cell, name, verts, faces, color, center, rot_z)


def prism(cell, name, center, radius, height, sides, color, phase=0.0, rot_z=0.0):
    """A regular flat-topped prism: cylinders, hexagons and round clumps at 16 px."""
    half = height / 2.0
    ring = []
    for index in range(sides):
        angle = math.radians(phase) + 2.0 * math.pi * index / sides
        ring.append((radius * math.cos(angle), radius * math.sin(angle)))
    verts = [(x, y, -half) for x, y in ring] + [(x, y, half) for x, y in ring]
    faces = [tuple(range(sides)), tuple(range(2 * sides - 1, sides - 1, -1))]
    for index in range(sides):
        following = (index + 1) % sides
        faces.append((index, following, following + sides, index + sides))
    return add_mesh(cell, name, verts, faces, color, center, rot_z)


class Cell:
    """One sprite's collection and the empty everything in it is parented to."""

    def __init__(self, cell_id, index):
        self.cell_id = cell_id
        self.collection = bpy.data.collections.new("cell/" + cell_id)
        bpy.context.scene.collection.children.link(self.collection)
        self.pivot = bpy.data.objects.new("pivot/" + cell_id, None)
        self.pivot.location = (
            (index % CELL_COLUMNS) * CELL_SPACING,
            -(index // CELL_COLUMNS) * CELL_SPACING,
            0.0,
        )
        self.pivot.empty_display_size = 0.5
        self.collection.objects.link(self.pivot)

    @property
    def origin(self):
        return tuple(self.pivot.location)


# --------------------------------------------------------------------------------------
# Terrain
# --------------------------------------------------------------------------------------

GROUND_HEIGHT = 0.08


def ground_plate(cell, color="ground", height=GROUND_HEIGHT):
    box(cell, cell.cell_id + "/plate", (0.0, 0.0, height / 2.0), (1.0, 1.0, height), color)
    return height


def build_terrain_empty(cell):
    top = ground_plate(cell)
    specks = ((-0.3, 0.26), (0.18, 0.34), (0.34, -0.1), (-0.12, -0.3), (-0.38, -0.08))
    for index, (x, y) in enumerate(specks):
        box(
            cell,
            "%s/speck%d" % (cell.cell_id, index),
            (x, y, top + 0.015),
            (0.09, 0.09, 0.03),
            "stone-shadow" if index % 2 else "tread",
        )


def build_terrain_stone(cell):
    top = ground_plate(cell, "stone-shadow", 0.06)
    for index, (x, y) in enumerate(((-0.25, 0.25), (0.25, 0.25), (-0.25, -0.25), (0.25, -0.25))):
        box(
            cell,
            "%s/block%d" % (cell.cell_id, index),
            (x, y, top + 0.11),
            (0.44, 0.44, 0.22),
            "stone",
        )
        box(
            cell,
            "%s/chip%d" % (cell.cell_id, index),
            (x + 0.1, y - 0.1, top + 0.23),
            (0.14, 0.14, 0.04),
            "stone-dark",
        )


def _brick_rows(cell, color, skip=()):
    top = ground_plate(cell, "brick-mortar", 0.06)
    rows = (0.375, 0.125, -0.125, -0.375)
    index = 0
    for row_index, y in enumerate(rows):
        if row_index % 2 == 0:
            spans = ((-0.245, 0.46), (0.245, 0.46))
        else:
            spans = ((-0.375, 0.2), (0.0, 0.46), (0.375, 0.2))
        for x, width in spans:
            if index not in skip:
                box(
                    cell,
                    "%s/brick%d" % (cell.cell_id, index),
                    (x, y, top + 0.09),
                    (width, 0.2, 0.18),
                    color,
                )
            index += 1
    return top


def build_terrain_brick(cell):
    _brick_rows(cell, "brick")


def build_terrain_brick_cracked(cell):
    top = _brick_rows(cell, "brick-dark", skip=(1, 5))
    rubble = ((0.3, 0.4, 0.1), (0.16, 0.33, 0.08), (0.42, 0.3, 0.07))
    for index, (x, y, size) in enumerate(rubble):
        box(
            cell,
            "%s/rubble%d" % (cell.cell_id, index),
            (x, y, top + 0.04),
            (size, size, 0.08),
            "brick-mortar",
        )
    crack = ((0.0, 0.3, 30.0), (-0.1, 0.1, -25.0), (0.04, -0.12, 35.0), (-0.06, -0.34, -20.0))
    for index, (x, y, angle) in enumerate(crack):
        box(
            cell,
            "%s/crack%d" % (cell.cell_id, index),
            (x, y, top + 0.2),
            (0.07, 0.3, 0.04),
            "crack",
            rot_z=angle,
        )


def build_terrain_mirror_ne(cell):
    _mirror(cell, -45.0)


def build_terrain_mirror_se(cell):
    _mirror(cell, 45.0)


def _mirror(cell, angle):
    top = ground_plate(cell, "stone-shadow", 0.06)
    box(cell, cell.cell_id + "/body", (0.0, 0.0, top + 0.11), (1.6, 0.34, 0.22), "mirror-dark",
        rot_z=angle)
    box(cell, cell.cell_id + "/face", (0.0, 0.0, top + 0.23), (1.6, 0.2, 0.05), "mirror",
        rot_z=angle)


WATER_PHASES = (
    ((-0.2, 0.3, 0.42), (0.2, 0.0, 0.5), (-0.1, -0.3, 0.3)),
    ((0.15, 0.36, 0.3), (-0.25, 0.06, 0.42), (0.2, -0.24, 0.46)),
    ((-0.05, 0.42, 0.46), (0.25, 0.12, 0.3), (-0.2, -0.18, 0.5)),
)


def _build_water(cell, phase):
    top = ground_plate(cell, "water-deep", 0.05)
    box(cell, cell.cell_id + "/surface", (0.0, 0.0, top + 0.02), (1.0, 1.0, 0.04), "water")
    for index, (x, y, width) in enumerate(WATER_PHASES[phase]):
        box(
            cell,
            "%s/crest%d" % (cell.cell_id, index),
            (x, y, top + 0.07),
            (width, 0.09, 0.05),
            "water-crest",
        )


def build_terrain_water_0(cell):
    _build_water(cell, 0)


def build_terrain_water_1(cell):
    _build_water(cell, 1)


def build_terrain_water_2(cell):
    _build_water(cell, 2)


FOREST_CLUMPS = (
    (-0.26, 0.26, 0.25, "forest"),
    (0.24, 0.3, 0.22, "forest-leaf"),
    (0.32, -0.2, 0.24, "forest"),
    (-0.3, -0.26, 0.23, "forest-mid"),
    (0.02, 0.04, 0.3, "forest-leaf"),
    (-0.04, 0.42, 0.18, "forest-dark"),
    (0.42, 0.06, 0.17, "forest-dark"),
    (-0.42, 0.0, 0.17, "forest"),
    (0.06, -0.42, 0.18, "forest-dark"),
)


def build_terrain_forest(cell):
    """Canopy only. The forest tile is an overlay: the ground has to show through it."""
    for index, (x, y, radius, color) in enumerate(FOREST_CLUMPS):
        prism(
            cell,
            "%s/clump%d" % (cell.cell_id, index),
            (x, y, 0.16),
            radius,
            0.18,
            10,
            color,
            phase=18.0 * index,
        )
        prism(
            cell,
            "%s/crown%d" % (cell.cell_id, index),
            (x - 0.04, y + 0.04, 0.26),
            radius * 0.52,
            0.06,
            8,
            "forest-leaf",
            phase=22.0 * index,
        )


def build_terrain_home_intact(cell):
    top = ground_plate(cell)
    box(cell, cell.cell_id + "/base", (0.0, -0.02, top + 0.05), (0.92, 0.92, 0.1), "stone-shadow")
    box(cell, cell.cell_id + "/inlay", (0.0, -0.02, top + 0.11), (0.76, 0.76, 0.04), "void")
    box(cell, cell.cell_id + "/body", (0.0, -0.06, top + 0.19), (0.3, 0.36, 0.14), "home")
    for index, sign in enumerate((-1.0, 1.0)):
        box(
            cell,
            "%s/wing%d" % (cell.cell_id, index),
            (0.27 * sign, 0.02, top + 0.18),
            (0.3, 0.12, 0.12),
            "home",
            rot_z=-24.0 * sign,
        )
        box(
            cell,
            "%s/foot%d" % (cell.cell_id, index),
            (0.14 * sign, -0.3, top + 0.16),
            (0.1, 0.14, 0.1),
            "home-dark",
        )
    box(cell, cell.cell_id + "/head", (0.0, 0.22, top + 0.21), (0.16, 0.16, 0.16), "home")
    box(cell, cell.cell_id + "/beak", (0.0, 0.34, top + 0.2), (0.08, 0.1, 0.1), "crack")


def build_terrain_home_destroyed(cell):
    top = ground_plate(cell)
    box(cell, cell.cell_id + "/pit", (0.0, -0.02, top + 0.02), (0.86, 0.86, 0.04), "void")
    shards = (
        (-0.3, 0.24, 0.22, 0.12, 34.0),
        (0.3, 0.2, 0.2, 0.1, -40.0),
        (-0.22, -0.26, 0.24, 0.12, -18.0),
        (0.26, -0.3, 0.18, 0.1, 22.0),
        (0.04, 0.02, 0.26, 0.14, 12.0),
    )
    for index, (x, y, width, height, angle) in enumerate(shards):
        box(
            cell,
            "%s/shard%d" % (cell.cell_id, index),
            (x, y, top + 0.06),
            (width, height, 0.08),
            "home-dark",
            rot_z=angle,
        )
    for index, (x, y) in enumerate(((-0.4, 0.4), (0.42, -0.08), (0.1, 0.42), (-0.1, -0.44))):
        box(
            cell,
            "%s/ash%d" % (cell.cell_id, index),
            (x, y, top + 0.04),
            (0.09, 0.09, 0.04),
            "ground-speck",
        )


# --------------------------------------------------------------------------------------
# Tanks. Built facing up; the render plan spins the cell pivot for the other three facings.
# --------------------------------------------------------------------------------------


def _treads(cell, width, length, color="tread"):
    for index, sign in enumerate((-1.0, 1.0)):
        box(
            cell,
            "%s/tread%d" % (cell.cell_id, index),
            (sign * (0.44 - width / 2.0), 0.0, 0.1),
            (width, length, 0.2),
            color,
        )
        for notch in range(4):
            box(
                cell,
                "%s/cleat%d%d" % (cell.cell_id, index, notch),
                (sign * (0.44 - width / 2.0), -0.3 + 0.2 * notch, 0.21),
                (width * 0.9, 0.07, 0.03),
                "stone-shadow",
            )


def build_tank_player(cell):
    _treads(cell, 0.22, 0.86)
    box(cell, cell.cell_id + "/hull", (0.0, -0.02, 0.17), (0.5, 0.72, 0.3), "player")
    box(cell, cell.cell_id + "/deck", (0.0, -0.02, 0.31), (0.44, 0.64, 0.04), "player")
    box(cell, cell.cell_id + "/glacis", (0.0, 0.3, 0.16), (0.36, 0.2, 0.22), "player-mid")
    box(cell, cell.cell_id + "/turret", (0.0, -0.06, 0.34), (0.36, 0.38, 0.12), "player")
    box(cell, cell.cell_id + "/barrel", (0.0, 0.32, 0.34), (0.12, 0.36, 0.12), "stone")
    for index, sign in enumerate((-1.0, 1.0)):
        box(
            cell,
            "%s/chevron%d" % (cell.cell_id, index),
            (0.11 * sign, 0.0, 0.33),
            (0.3, 0.1, 0.05),
            "shot-player",
            rot_z=-38.0 * sign,
        )


def build_tank_enemy_normal(cell):
    _treads(cell, 0.22, 0.86)
    box(cell, cell.cell_id + "/hull", (0.0, -0.02, 0.17), (0.5, 0.72, 0.3), "enemy-mid")
    box(cell, cell.cell_id + "/glacis", (0.0, 0.3, 0.16), (0.4, 0.18, 0.22), "enemy-dark")
    prism(cell, cell.cell_id + "/turret", (0.0, -0.06, 0.34), 0.21, 0.12, 8, "enemy-dark")
    box(cell, cell.cell_id + "/barrel", (0.0, 0.32, 0.34), (0.12, 0.36, 0.12), "stone-dark")
    prism(cell, cell.cell_id + "/eye", (0.0, -0.08, 0.41), 0.08, 0.04, 8, "shot-enemy")


def build_tank_enemy_shielded(cell):
    build_tank_enemy_normal(cell)
    plates = (
        (0.0, 0.45, 0.78, 0.08),
        (-0.47, -0.04, 0.06, 0.9),
        (0.47, -0.04, 0.06, 0.9),
    )
    for index, (x, y, width, height) in enumerate(plates):
        box(
            cell,
            "%s/plate%d" % (cell.cell_id, index),
            (x, y, 0.19),
            (width, height, 0.38),
            "stone-dark",
        )
    rivets = ((-0.28, 0.45), (0.28, 0.45), (-0.47, 0.26), (0.47, 0.26), (-0.47, -0.3), (0.47, -0.3))
    for index, (x, y) in enumerate(rivets):
        box(
            cell,
            "%s/rivet%d" % (cell.cell_id, index),
            (x, y, 0.4),
            (0.1, 0.1, 0.06),
            "shot-enemy",
        )


def build_tank_enemy_unshielded(cell):
    _treads(cell, 0.15, 0.78, "enemy-dark")
    for index, x in enumerate((-0.14, 0.0, 0.14)):
        box(
            cell,
            "%s/strut%d" % (cell.cell_id, index),
            (x, -0.04, 0.16),
            (0.1, 0.66, 0.28),
            "enemy-dark",
        )
    box(cell, cell.cell_id + "/spine", (0.0, 0.0, 0.3), (0.42, 0.12, 0.08), "enemy")
    box(cell, cell.cell_id + "/core", (0.0, -0.12, 0.3), (0.2, 0.22, 0.1), "enemy")
    box(cell, cell.cell_id + "/barrel", (0.0, 0.34, 0.3), (0.1, 0.34, 0.1), "stone-dark")


# --------------------------------------------------------------------------------------
# Projectile, powerups, effects
# --------------------------------------------------------------------------------------


def build_shot(cell):
    box(cell, cell.cell_id + "/core", (0.0, 0.06, 0.2), (0.18, 0.3, 0.18), "shot-player")
    prism(cell, cell.cell_id + "/nose", (0.0, 0.26, 0.2), 0.1, 0.16, 3, "crack", phase=90.0)
    for index, sign in enumerate((-1.0, 1.0)):
        box(
            cell,
            "%s/fin%d" % (cell.cell_id, index),
            (0.1 * sign, -0.1, 0.18),
            (0.07, 0.14, 0.12),
            "shot-enemy",
        )
    box(cell, cell.cell_id + "/trail", (0.0, -0.22, 0.18), (0.1, 0.2, 0.1), "shot-enemy")
    box(cell, cell.cell_id + "/spark", (0.0, -0.36, 0.18), (0.06, 0.1, 0.08), "text-dim")


def _powerup_frame(cell):
    """A hollow ring. The interior stays transparent so the icon owns the silhouette."""
    for index, (x, y, width, height) in enumerate(
        ((0.0, 0.42, 0.92, 0.12), (0.0, -0.42, 0.92, 0.12),
         (-0.42, 0.0, 0.12, 0.72), (0.42, 0.0, 0.12, 0.72))
    ):
        box(
            cell,
            "%s/frame%d" % (cell.cell_id, index),
            (x, y, 0.08),
            (width, height, 0.16),
            "powerup-frame",
        )


def build_powerup_gatling(cell):
    _powerup_frame(cell)
    for index, x in enumerate((-0.16, 0.0, 0.16)):
        box(
            cell,
            "%s/barrel%d" % (cell.cell_id, index),
            (x, 0.06, 0.14),
            (0.11, 0.46, 0.16),
            "stone" if index == 1 else "stone-dark",
        )
    box(cell, cell.cell_id + "/breech", (0.0, -0.2, 0.14), (0.48, 0.16, 0.16), "stone-dark")


def build_powerup_invincibility(cell):
    _powerup_frame(cell)
    prism(cell, cell.cell_id + "/boss", (0.0, 0.0, 0.12), 0.27, 0.16, 6, "mirror", phase=30.0)
    prism(cell, cell.cell_id + "/core", (0.0, 0.0, 0.21), 0.13, 0.08, 6, "player-dark", phase=30.0)


def build_powerup_extra_life(cell):
    _powerup_frame(cell)
    box(cell, cell.cell_id + "/arm-x", (0.0, 0.0, 0.12), (0.5, 0.17, 0.16), "shot-player")
    box(cell, cell.cell_id + "/arm-y", (0.0, 0.0, 0.12), (0.17, 0.5, 0.16), "shot-player")


def _ring(cell, count, radius, length, width, color, phase, z=0.1):
    for index in range(count):
        angle = math.radians(phase) + 2.0 * math.pi * index / count
        box(
            cell,
            "%s/spoke%d" % (cell.cell_id, index),
            (radius * math.cos(angle), radius * math.sin(angle), z),
            (length, width, 0.1),
            color,
            rot_z=math.degrees(angle),
        )


def build_effect_explosion_0(cell):
    prism(cell, cell.cell_id + "/core", (0.0, 0.0, 0.1), 0.2, 0.12, 8, "crack")
    _ring(cell, 4, 0.26, 0.14, 0.14, "powerup-frame", 45.0)


def build_effect_explosion_1(cell):
    prism(cell, cell.cell_id + "/core", (0.0, 0.0, 0.1), 0.14, 0.12, 8, "crack")
    _ring(cell, 6, 0.28, 0.3, 0.12, "powerup-frame", 0.0)


def build_effect_explosion_2(cell):
    prism(cell, cell.cell_id + "/core", (0.0, 0.0, 0.1), 0.09, 0.1, 6, "powerup-frame")
    _ring(cell, 8, 0.36, 0.34, 0.1, "danger", 22.5)


def build_effect_explosion_3(cell):
    embers = (
        (-0.4, 0.34), (0.36, 0.4), (0.44, -0.18), (-0.3, -0.4),
        (0.08, 0.46), (-0.46, -0.04), (0.2, -0.44), (-0.12, 0.18),
        (0.24, 0.1), (-0.2, -0.14),
    )
    for index, (x, y) in enumerate(embers):
        box(
            cell,
            "%s/ember%d" % (cell.cell_id, index),
            (x, y, 0.1),
            (0.11, 0.11, 0.1),
            "danger" if index % 2 else "powerup-frame",
            rot_z=45.0,
        )


def _wedges(cell, radius, size, color):
    for index in range(4):
        angle = math.radians(45.0 + 90.0 * index)
        box(
            cell,
            "%s/wedge%d" % (cell.cell_id, index),
            (radius * math.cos(angle), radius * math.sin(angle), 0.1),
            (size, size, 0.1),
            color,
            rot_z=45.0,
        )


def build_effect_spawn_0(cell):
    _wedges(cell, 0.42, 0.17, "player-dark")


def build_effect_spawn_1(cell):
    _wedges(cell, 0.26, 0.22, "player")


def build_effect_spawn_2(cell):
    _wedges(cell, 0.1, 0.26, "shot-player")


def build_effect_shield_0(cell):
    _ring(cell, 6, 0.4, 0.14, 0.2, "mirror", 0.0)


def build_effect_shield_1(cell):
    _ring(cell, 6, 0.4, 0.14, 0.2, "mirror", 30.0)


BUILDERS = {
    "terrain-empty": build_terrain_empty,
    "terrain-stone": build_terrain_stone,
    "terrain-brick": build_terrain_brick,
    "terrain-brick-cracked": build_terrain_brick_cracked,
    "terrain-mirror-ne": build_terrain_mirror_ne,
    "terrain-mirror-se": build_terrain_mirror_se,
    "terrain-water-0": build_terrain_water_0,
    "terrain-water-1": build_terrain_water_1,
    "terrain-water-2": build_terrain_water_2,
    "terrain-forest": build_terrain_forest,
    "terrain-home-intact": build_terrain_home_intact,
    "terrain-home-destroyed": build_terrain_home_destroyed,
    "tank-player": build_tank_player,
    "tank-enemy-normal": build_tank_enemy_normal,
    "tank-enemy-shielded": build_tank_enemy_shielded,
    "tank-enemy-unshielded": build_tank_enemy_unshielded,
    "shot": build_shot,
    "powerup-gatling": build_powerup_gatling,
    "powerup-invincibility": build_powerup_invincibility,
    "powerup-extra-life": build_powerup_extra_life,
    "effect-explosion-0": build_effect_explosion_0,
    "effect-explosion-1": build_effect_explosion_1,
    "effect-explosion-2": build_effect_explosion_2,
    "effect-explosion-3": build_effect_explosion_3,
    "effect-spawn-0": build_effect_spawn_0,
    "effect-spawn-1": build_effect_spawn_1,
    "effect-spawn-2": build_effect_spawn_2,
    "effect-shield-0": build_effect_shield_0,
    "effect-shield-1": build_effect_shield_1,
}


# --------------------------------------------------------------------------------------
# Scene assembly
# --------------------------------------------------------------------------------------


def clear_scene():
    """Start from an empty file so nothing from the startup scene reaches a render."""
    for collection in (
        bpy.data.objects,
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.lights,
        bpy.data.cameras,
        bpy.data.collections,
        bpy.data.worlds,
    ):
        for datablock in list(collection):
            collection.remove(datablock)
    MATERIALS.clear()


def aim(direction):
    """Euler angles that point a light's -Z axis along ``direction``."""
    from mathutils import Vector

    return Vector(direction).normalized().to_track_quat("-Z", "Y").to_euler()


def add_lights():
    world = bpy.data.worlds.new("starter-world")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (*WORLD_COLOR, 1.0)
    background.inputs["Strength"].default_value = WORLD_STRENGTH
    bpy.context.scene.world = world

    for name, direction, energy in (
        ("key", KEY_DIRECTION, KEY_ENERGY),
        ("fill", FILL_DIRECTION, FILL_ENERGY),
    ):
        light = bpy.data.lights.new("sun-" + name, type="SUN")
        light.energy = energy
        light.angle = 0.0
        obj = bpy.data.objects.new("sun-" + name, light)
        obj.location = (0.0, 0.0, 4.0)
        obj.rotation_euler = aim(direction)
        bpy.context.scene.collection.objects.link(obj)


def add_camera():
    camera = bpy.data.cameras.new("sprite-camera")
    camera.type = "ORTHO"
    camera.ortho_scale = 1.0
    camera.clip_start = 0.1
    camera.clip_end = 100.0
    obj = bpy.data.objects.new("sprite-camera", camera)
    obj.location = (0.0, 0.0, CAMERA_HEIGHT)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.scene.camera = obj
    return obj


def pin_render_settings():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = CYCLES_SAMPLES
    scene.cycles.seed = CYCLES_SEED
    scene.cycles.use_animated_seed = False
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = MAX_BOUNCES
    scene.cycles.diffuse_bounces = MAX_BOUNCES
    scene.cycles.glossy_bounces = 0
    scene.cycles.transmission_bounces = 0
    scene.cycles.volume_bounces = 0
    scene.cycles.transparent_max_bounces = MAX_BOUNCES
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.cycles.pixel_filter_type = "BLACKMAN_HARRIS"
    scene.cycles.filter_width = 1.0
    scene.cycles.film_transparent_glass = False

    scene.render.resolution_x = RENDER_PIXELS
    scene.render.resolution_y = RENDER_PIXELS
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.use_motion_blur = False
    scene.render.use_compositing = False
    scene.render.use_sequencer = False
    scene.render.use_overwrite = True
    scene.render.use_file_extension = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15

    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.display_settings.display_device = "sRGB"
    scene.render.dither_intensity = 0.0
    scene.sequencer_colorspace_settings.name = "sRGB"

    scene.frame_start = 1
    scene.frame_end = 1
    scene.frame_set(1)


def build():
    clear_scene()
    pin_render_settings()
    add_lights()
    add_camera()
    for index, cell_id in enumerate(CELLS):
        cell = Cell(cell_id, index)
        BUILDERS[cell_id](cell)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="build_scene.py", description=__doc__)
    parser.add_argument("--output", required=True, help="where to save the .blend")
    return parser.parse_args(argv)


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    options = parse_args(argv)
    build()
    bpy.ops.wm.save_as_mainfile(filepath=options.output, compress=True, copy=False)
    print("built %d cells into %s" % (len(CELLS), options.output))


if __name__ == "__main__":
    main()
