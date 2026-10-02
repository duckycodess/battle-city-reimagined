# Blender source for the starter sprite pack

This directory is the art pipeline's source of truth. It holds the script that builds the
scene, the script that renders it, and a saved `.blend` for convenience. Everything in
`assets/sprites/starter/` is generated from these three files plus
`battle_city_tools.assets`; nothing there is drawn, traced or touched up by hand.

## What is in here

| File | What it is |
| --- | --- |
| `build_scene.py` | **The versioned source.** Builds every sprite's geometry, materials, lights and camera, pins the render settings and saves the `.blend`. |
| `render_frames.py` | Renders one frame per entry in `RENDER_PLAN`, one cell at a time, and writes `render.json` describing what actually rendered. |
| `starter_set.blend` | A **derived convenience artifact**: the scene `build_scene.py` produces, so it can be opened and looked at. See the determinism note below -- it is explicitly outside the reproducibility guarantee. |

## Reproducing the pack

Three commands. The first two need Blender; the third does not.

```sh
# 1. Build the scene. Overwrites starter_set.blend.
blender --background --factory-startup \
    --python assets/blender/build_scene.py -- --output assets/blender/starter_set.blend

# 2. Render every frame into a scratch directory. Never into the repository.
blender --background --factory-startup assets/blender/starter_set.blend \
    --python assets/blender/render_frames.py -- --output /tmp/bc-frames

# 3. Pack, validate and draw the previews. This is the only step that writes into the repo.
uv run --locked --package battle-city-tools python -m battle_city_tools.assets \
    build --frames /tmp/bc-frames --output assets/sprites/starter
uv run --locked --package battle-city-tools python -m battle_city_tools.assets validate
```

Step 2 takes about 45 seconds on eight cores and needs no GPU and no display. Step 3 needs
neither Blender nor an image library: it is standard library plus `battle_city_content`.

To check reproducibility, run step 2 twice into two directories, run step 3 into two
scratch directories, and compare. See "What is and is not reproducible" below for what to
expect. Note that step 3 refuses a render directory holding anything the current catalogue
did not render, so render into an empty directory rather than over an old one -- that is
the check that stops a renamed sprite's stale file from being mistaken for a current one.

## The environment this pack was rendered in

| | |
| --- | --- |
| Blender | 4.5.14 LTS, build hash `62c1db4208e8`, build date 2026-09-15, Linux x64 |
| Obtained from | `https://download.blender.org/release/Blender4.5/blender-4.5.14-linux-x64.tar.xz` |
| Archive SHA-256 | `9ba871ff2ecd36526b77432745980b7e6664ecd0c7ca11c48849073dcfe06da3` |
| Blender's Python | 3.11.15 |
| Host | Linux 6.6 (WSL2) on x86-64, no GPU used |
| Installed | Extracted to a scratch directory. **No Blender binary is vendored in this repository**, and nothing outside `assets/**`, `packages/tools/src/battle_city_tools/assets/**` and `tests/assets/**` was changed to run it. |

Blender is not a build dependency of this project. Continuous integration never renders;
it validates the checked-in atlas with the standard library alone.

## Pinned render settings

`render_frames.py` reads these back out of Blender after the scene is loaded and writes
them to `render.json`, and the packer copies them verbatim into `assets/sprites/starter/atlas.json`
under `render`. The record therefore describes what rendered, not what was requested.

| Setting | Value |
| --- | --- |
| Engine / device | `CYCLES` on `CPU`, 1 render thread (`threads_mode` `FIXED`) |
| Samples / seed | 96 / 0, adaptive sampling **off**, denoising **off** |
| Bounces | `max_bounces` 1, `diffuse_bounces` 1, glossy and transmission 0, caustics off |
| Pixel filter | `BLACKMAN_HARRIS`, width 1.0 |
| Film | `film_transparent` on, no motion blur, no compositor, no sequencer |
| Resolution | 64x64 at 100% -- a 16-pixel frame supersampled 4x |
| Camera | `ORTHO`, `ortho_scale` 1.0, at z 6.0, rotation (0, 0, 0), clip 0.1 to 100.0 |
| Colour management | view transform `Standard`, look `None`, display `sRGB`, exposure 0, gamma 1, dither 0, sequencer `sRGB` |
| Output | PNG, `RGBA`, 8-bit, compression 15 |
| World | colour (0.16, 0.18, 0.24) at strength 0.35 |
| Key light | sun, energy 2.9, angle 0, direction (0.45, -0.45, -0.77) |
| Fill light | sun, energy 0.9, angle 0, direction (-0.35, 0.3, -0.89) |

`Standard` rather than AgX or Filmic, and dither 0, because the materials are the shipped
palette and the render is supposed to come back out as those colours rather than as a
tone-mapped photograph of them.

After Blender exits, `battle_city_tools.assets.build` does three more fixed things, also
recorded in the sidecar under `post.*`: a 4x box downsample with premultiplied coverage,
an alpha snap to binary at a threshold of 128, and a nearest-neighbour quantisation into
the 43-colour palette.

## Scene conventions

* **One world unit is one tile**, and one tile is 16 pixels. Every frame is 16x16; smaller
  art sits inside a frame of that size.
* **One cell per sprite.** Each sprite is a collection `cell/<id>` containing an empty
  `pivot/<id>` with all of that sprite's geometry parented to it. At render time every
  other cell is hidden, so neighbours cannot leak light, shadow or pixels.
* **Facings rotate the object, not the camera.** A tank is modelled facing up; the render
  plan spins its pivot about Z. Zero degrees is up, +90 is left, 180 is down, -90 is right.
  The lights never move, so the key stays at the frame's upper left on every sprite.
* **Mirrors are drawn by their geometry, not by their names.**
  `battle_city_sim.tiles.MIRROR_REFLECTIONS` records that the historical tile names read
  backwards: `MIRROR_NE` (tile 3) sends a rightward shot *downward*, which is a `\`
  surface, and `MIRROR_SE` (tile 4) sends it *upward*, which is a `/`. The art follows the
  deflection, because art that followed the name would teach a player to predict the wrong
  bounce. Renaming the tiles would need a content proposal and is not this pack's call.
  `tests/assets` asserts the lean mechanically.
* **Colours are the palette.** Every material is a colour from
  `battle_city_tools.assets.palette`, which is in turn the client's
  `battle_city_client.theme` plus the mid tones a lit render needs. A test parses this
  directory's palette table and fails if it drifts.

## What is and is not reproducible

**Reproducible: the pixels, and therefore everything tracked in git.** Rendering twice
from two independently built `.blend` files and packing both produced byte-identical
`atlas.png`, `atlas.json`, `preview.png` and `stage-composite.png`. At the frame level the
two runs' PNG `IDAT` streams were also byte-identical for all 44 frames; Cycles was exactly
deterministic here.

This was re-run end to end during review, on the environment recorded above: two scene
builds, two renders, two packs. All four tracked outputs came back byte-identical to each
other **and to the files checked in here**, all 44 `IDAT` streams matched across the two
renders, and all 44 render files differed as files. So the checked-in artifacts are
reproducible from the scripts in this directory, not merely self-consistent.

One honest limit on that claim: it is one machine, one Blender build, one CPU. Cycles
being bit-exact across two runs on the same host does not establish that it is bit-exact
across hosts, and nothing in this repository depends on it being so -- continuous
integration never renders, and the sidecar's digests are over pixels the packer produced,
not over anything Blender wrote to disk.

**Not reproducible: Blender's intermediate PNG files as files.** Those 44 renders differ
between runs anyway, because Blender stamps `tEXt` chunks holding the output path, the
date and the render time. This is why nothing downstream trusts a file hash of a render.

**Not reproducible: `starter_set.blend`.** Two builds of the same scene from the same
script produced different bytes. A `.blend` embeds build metadata and pointer layout; it
is not a reproducible artifact and is not treated as one. `build_scene.py` is the source.
If the two ever disagree, the script wins -- delete the `.blend` and rebuild it.

**Integrity in the sidecar is a digest of pixels, never of file bytes.** `atlas.json`
records `sha256_pixels` for the sheet and for every frame, over the dimensions and the
RGBA bytes. A digest of `atlas.png` would be a promise this pipeline cannot keep, because
zlib output may differ between zlib builds. The sidecar does not carry a digest of itself:
a document cannot contain its own hash, and pretending otherwise would mean excluding a
field from the thing it claims to cover. Its integrity is git's.

## Authorship and licence

Every mesh, material and light in this pack was authored by `build_scene.py` in this
repository. No artwork, texture or sprite sheet from the historical
`duckycodess/Battle-City` repository was copied, traced or re-encoded, and no
AI-generated image is a runtime dependency of this pack -- the art pipeline specification
allows generated images as concept references only, and none were used as source here.

Authors: Battle City Reimagined contributors.
SPDX: `NOASSERTION`. The repository publishes no licence file, so this pack asserts none
on its owner's behalf, matching the bundled content pack's stance.

## Known gaps

* **Effects carry no timing.** `effect-explosion-*`, `effect-spawn-*` and `effect-shield-*`
  ship as named sequences with no durations and no trigger conditions, and the water
  phases likewise. No accepted specification sets effect or animation timing, so inventing
  one here would create a gameplay contract out of an art asset. That belongs in a change
  proposal.
* **No in-game capture.** The only seam that could render these sprites through the real
  client is its `AssetLibrary` protocol, which this change may not touch.
  `assets/sprites/starter/stage-composite.png` is a labelled pipeline composite and says so
  in its own pixels; it is not a screenshot.
* **No UI symbols.** The art pipeline specification lists UI symbols among the things the
  pipeline renders, and this pack ships none. That is deliberate rather than overlooked:
  this issue's goal names terrain, tanks, the base, projectiles, powerups and effects, and
  a UI glyph set cannot be designed without the HUD that consumes it. The pipeline needs no
  change to carry them -- a glyph is a cell in the scene and a line in the catalogue -- so
  they belong to whichever issue builds the HUD.
* **The thresholds under `readability` are this pack's own.** They are regression guards
  measured off the shipped art, not minima any specification sets, and clearing them is
  not a conformance claim against an accessibility standard. See
  `../sprites/starter/README.md` and the module docstring in `catalog.py`.
