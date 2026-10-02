"""Render every starter frame from ``starter_set.blend``, one cell at a time.

Run with Blender, never with the project interpreter::

    blender --background --factory-startup assets/blender/starter_set.blend \
        --python assets/blender/render_frames.py -- --output <directory>

The output directory is scratch, not the repository: it receives one supersampled PNG per
frame plus ``render.json``, and ``python -m battle_city_tools.assets build`` turns those
into the atlas, the sidecar and the previews that are tracked in git.

What this script is responsible for
-----------------------------------
*One cell at a time.* Every collection except the one being rendered is hidden, and the
camera is moved onto that cell's origin. Nothing else can contribute a pixel, a shadow or
a bounce, which is what makes a frame independent of where its cell sits in the grid and
of which frames were rendered before it.

*Facings are the object turning, not the camera.* A tank is modelled facing up and the
cell's pivot is spun about Z for the other three. The lights never move, so the key stays
at the frame's upper left for every sprite in the pack.

*The record is read back, not asserted.* ``render.json`` is written from the values
Blender actually holds at render time, including its own version and build hash. If a
setting fails to apply, the record says what really happened rather than what was asked
for, and the packer copies that record into the sidecar.

Determinism notes. Cycles runs on the CPU with a fixed seed, adaptive sampling off and
denoising off, because OpenImageDenoise output varies with the CPU and the build. A single
render thread is pinned for the same reason: it costs nothing at 64x64 and removes the
question entirely. Re-rendering into two directories and comparing is part of the
documented procedure in ``README.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import bpy

RENDER_PLAN = (
    ("effect-explosion-0", "effect-explosion-0", 0),
    ("effect-explosion-1", "effect-explosion-1", 0),
    ("effect-explosion-2", "effect-explosion-2", 0),
    ("effect-explosion-3", "effect-explosion-3", 0),
    ("effect-shield-0", "effect-shield-0", 0),
    ("effect-shield-1", "effect-shield-1", 0),
    ("effect-spawn-0", "effect-spawn-0", 0),
    ("effect-spawn-1", "effect-spawn-1", 0),
    ("effect-spawn-2", "effect-spawn-2", 0),
    ("powerup-extra-life", "powerup-extra-life", 0),
    ("powerup-gatling", "powerup-gatling", 0),
    ("powerup-invincibility", "powerup-invincibility", 0),
    ("shot-down", "shot", 180),
    ("shot-left", "shot", 90),
    ("shot-right", "shot", -90),
    ("shot-up", "shot", 0),
    ("tank-enemy-normal-down", "tank-enemy-normal", 180),
    ("tank-enemy-normal-left", "tank-enemy-normal", 90),
    ("tank-enemy-normal-right", "tank-enemy-normal", -90),
    ("tank-enemy-normal-up", "tank-enemy-normal", 0),
    ("tank-enemy-shielded-down", "tank-enemy-shielded", 180),
    ("tank-enemy-shielded-left", "tank-enemy-shielded", 90),
    ("tank-enemy-shielded-right", "tank-enemy-shielded", -90),
    ("tank-enemy-shielded-up", "tank-enemy-shielded", 0),
    ("tank-enemy-unshielded-down", "tank-enemy-unshielded", 180),
    ("tank-enemy-unshielded-left", "tank-enemy-unshielded", 90),
    ("tank-enemy-unshielded-right", "tank-enemy-unshielded", -90),
    ("tank-enemy-unshielded-up", "tank-enemy-unshielded", 0),
    ("tank-player-down", "tank-player", 180),
    ("tank-player-left", "tank-player", 90),
    ("tank-player-right", "tank-player", -90),
    ("tank-player-up", "tank-player", 0),
    ("terrain-brick", "terrain-brick", 0),
    ("terrain-brick-cracked", "terrain-brick-cracked", 0),
    ("terrain-empty", "terrain-empty", 0),
    ("terrain-forest", "terrain-forest", 0),
    ("terrain-home-destroyed", "terrain-home-destroyed", 0),
    ("terrain-home-intact", "terrain-home-intact", 0),
    ("terrain-mirror-ne", "terrain-mirror-ne", 0),
    ("terrain-mirror-se", "terrain-mirror-se", 0),
    ("terrain-stone", "terrain-stone", 0),
    ("terrain-water-0", "terrain-water-0", 0),
    ("terrain-water-1", "terrain-water-1", 0),
    ("terrain-water-2", "terrain-water-2", 0),
)
"""``(frame name, cell id, degrees about Z)``, sorted by frame name.

Zero degrees is facing up. Ninety is a counter-clockwise quarter turn, which points a
sprite left, because the camera looks down -Z with +Y up the frame. ``tests/assets``
checks this table against the shipped catalogue and against the cells the build script
creates, so a renamed sprite cannot quietly stop being rendered.
"""

MANIFEST_FILENAME = "render.json"


def hide_all_cells():
    for collection in bpy.data.collections:
        if collection.name.startswith("cell/"):
            collection.hide_render = True


def cell_collection(cell_id):
    name = "cell/" + cell_id
    collection = bpy.data.collections.get(name)
    if collection is None:
        raise SystemExit("the scene has no collection named %r" % name)
    return collection


def cell_pivot(cell_id):
    name = "pivot/" + cell_id
    pivot = bpy.data.objects.get(name)
    if pivot is None:
        raise SystemExit("the scene has no pivot named %r" % name)
    return pivot


def render_frame(frame_name, cell_id, degrees, output_dir):
    scene = bpy.context.scene
    collection = cell_collection(cell_id)
    pivot = cell_pivot(cell_id)
    pivot.rotation_euler = (0.0, 0.0, math.radians(degrees))
    collection.hide_render = False
    scene.camera.location = (pivot.location.x, pivot.location.y, scene.camera.location.z)
    scene.render.filepath = os.path.join(output_dir, frame_name)
    bpy.ops.render.render(write_still=True)
    collection.hide_render = True
    pivot.rotation_euler = (0.0, 0.0, 0.0)


def pin_threads():
    """One render thread, so no scheduling decision can reach the pixels."""
    bpy.context.scene.render.threads_mode = "FIXED"
    bpy.context.scene.render.threads = 1


def record():
    """Read the settings back out of Blender, rather than restating what was asked for."""
    scene = bpy.context.scene
    camera = scene.camera
    data = camera.data
    world = scene.world.node_tree.nodes["Background"]
    color = world.inputs["Color"].default_value
    return {
        "blender.build_date": bpy.app.build_date.decode("utf-8"),
        "blender.build_hash": bpy.app.build_hash.decode("utf-8"),
        "blender.build_platform": bpy.app.build_platform.decode("utf-8"),
        "blender.version": bpy.app.version_string,
        "camera.clip_end": round(data.clip_end, 6),
        "camera.clip_start": round(data.clip_start, 6),
        "camera.location_x": 0.0,
        "camera.location_y": 0.0,
        "camera.location_z": round(camera.location.z, 6),
        "camera.ortho_scale": round(data.ortho_scale, 6),
        "camera.rotation_x": round(camera.rotation_euler.x, 6),
        "camera.rotation_y": round(camera.rotation_euler.y, 6),
        "camera.rotation_z": round(camera.rotation_euler.z, 6),
        "camera.type": data.type,
        "color.dither": round(scene.render.dither_intensity, 6),
        "color.display_device": scene.display_settings.display_device,
        "color.exposure": round(scene.view_settings.exposure, 6),
        "color.gamma": round(scene.view_settings.gamma, 6),
        "color.look": scene.view_settings.look,
        "color.sequencer_colorspace": scene.sequencer_colorspace_settings.name,
        "color.view_transform": scene.view_settings.view_transform,
        "cycles.adaptive_sampling": bool(scene.cycles.use_adaptive_sampling),
        "cycles.denoising": bool(scene.cycles.use_denoising),
        "cycles.device": scene.cycles.device,
        "cycles.diffuse_bounces": scene.cycles.diffuse_bounces,
        "cycles.max_bounces": scene.cycles.max_bounces,
        "cycles.samples": scene.cycles.samples,
        "cycles.seed": scene.cycles.seed,
        "engine": scene.render.engine,
        "filter_width": round(scene.cycles.filter_width, 6),
        "output.color_depth": scene.render.image_settings.color_depth,
        "output.color_mode": scene.render.image_settings.color_mode,
        "output.compression": scene.render.image_settings.compression,
        "output.file_format": scene.render.image_settings.file_format,
        "pixel_filter": scene.cycles.pixel_filter_type,
        "render.resolution_percentage": scene.render.resolution_percentage,
        "render.resolution_x": scene.render.resolution_x,
        "render.resolution_y": scene.render.resolution_y,
        "render.threads": scene.render.threads,
        "render.threads_mode": scene.render.threads_mode,
        "world.color_b": round(color[2], 6),
        "world.color_g": round(color[1], 6),
        "world.color_r": round(color[0], 6),
        "world.strength": round(world.inputs["Strength"].default_value, 6),
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="render_frames.py", description=__doc__)
    parser.add_argument("--output", required=True, help="scratch directory for the frames")
    return parser.parse_args(argv)


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    options = parse_args(argv)
    output_dir = os.path.abspath(options.output)
    os.makedirs(output_dir, exist_ok=True)

    if not bpy.context.scene.render.film_transparent:
        raise SystemExit("the opened scene does not render a transparent film")
    pin_threads()
    hide_all_cells()
    for frame_name, cell_id, degrees in RENDER_PLAN:
        render_frame(frame_name, cell_id, degrees, output_dir)
        print("rendered %s" % frame_name)

    manifest = {"frames": [row[0] for row in RENDER_PLAN], "render": record()}
    with open(os.path.join(output_dir, MANIFEST_FILENAME), "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("wrote %d frames and %s" % (len(RENDER_PLAN), MANIFEST_FILENAME))


if __name__ == "__main__":
    main()
