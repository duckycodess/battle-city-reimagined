# Art pipeline specification

## Source of truth

Final game art comes from versioned Blender scenes rendered with a consistent orthographic camera and controlled lighting/palette. Deterministic scripts export spritesheets and metadata. AI-generated images may guide visual exploration only; one-off generated sprites must not enter shipped packs.

## Pipeline stages

1. Maintain reusable Blender assets and source scenes.
2. Render directional tanks, terrain, projectiles, powerups, effects, UI symbols, and animation frames with pinned camera, scale, palette, and color-management settings.
3. Pack renders deterministically into sheets with explicit frame dimensions, pivots, animation names, palette, and source identifiers.
4. Validate dimensions, transparent bounds, duplicate frames, metadata references, and asset/license declarations.
5. Produce reproducible artifacts in CI or a documented local Blender environment.

## Visual direction

Use a cohesive stylized 2D/2.5D look: readable silhouettes, strong terrain contrast, clear team/faction colors, and deliberate effects that stay legible at gameplay scale. Rendering may imply depth, but gameplay geometry remains grid- and simulation-owned.

The v2 conveyor/pad presentation uses five deterministic, grayscale-distinct static glyphs in the existing procedural client renderer. That renderer does not consume the Blender atlas, so this change does not add unused Blender/atlas assets; future atlas integration must follow the source and export rules above. Glyphs do not change simulation hitboxes.

## Requirements

- Camera, lighting, render resolution, color management, and Blender version are recorded.
- Export order and atlas layout are deterministic.
- Sprite pivots and hitboxes are separate metadata; art cannot change collision geometry.
- Forest cover, water animation, mirror direction, team, damage, and invincibility cues remain readable without color alone.
- Source assets and outputs carry authorship and license metadata.
- Concept images are references, never runtime dependencies.
