# battle_city_client

The pygame-ce client: window, input, presentation. It renders simulation state and
translates keystrokes into tick-indexed simulation commands. It owns no game rules.

## Launch it

From the repository root:

```sh
uv run --locked --package battle-city-client python -m battle_city_client
```

Options: `--seed`, `--scale` (1-8), `--frame-cap`. `--help` lists them.

Headless, with no window and no sound device:

```sh
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  uv run --locked --package battle-city-client python -m battle_city_client
```

That is the same path the tests take. `tests/client` sets both drivers before importing
pygame and drives the real loop, the real renderer and the real simulation.

## Controls

| Action       | Keys            |
| ------------ | --------------- |
| Move         | Arrows or WASD  |
| Fire         | Space or `J`    |
| Pause/resume | `ESC` or `P`    |
| Menu select  | `Enter`         |
| Menu back    | `ESC`           |
| Window scale | `-` and `+`     |
| Quit         | Close the window, or `QUIT` on the main menu |

Keyboard only in this build. Gamepad support and remapping are accessibility
requirements; they arrive as further tables feeding the same `Action` vocabulary in
`keymap.py`, and nothing downstream of that module knows what a device is.

## What it does and does not simulate

Movement, firing, brick and cracked-brick damage, mirror reflection, water, forest cover
and the home base are all the simulation's, driven at a fixed 60 ticks per second
independently of how fast the window redraws.

There are **no enemies, no waves and no stage victory**. The simulation ships no enemy
steering and no wave cadence -- enemy tanks are actors that move only when commanded --
and the product specification defers wave pacing and win-state timing to an accepted
gameplay proposal. Adding either in the client would put game rules in the presentation
layer, so the client does not. Enemy behaviour arrives with the AI phase and campaign
rules with the campaign phase.

The two outcomes the simulation can record, `BASE_DESTROYED` and `PLAYERS_ELIMINATED`,
both need a hostile projectile, so no live run can reach one yet. The terminal screen is
still implemented and still tested: `tests/client` assembles a finished state and renders
it. Every such capture is labelled a test fixture. The client itself never sets an
outcome; it only reads one.

## Replacing the art

Every pixel the renderer draws comes from an `AssetLibrary` (see `assets.py`).
`ProceduralAssetLibrary` draws stand-ins from primitives at exactly the sizes the
simulation's rules define. The art-pipeline specification calls for Blender-rendered
spritesheets with explicit frame sizes and pivots; shipping those means implementing the
same protocol over an atlas. The renderer, the layout and the simulation do not change,
and art still cannot change collision geometry -- the renderer positions everything from
the body the simulation collides with.

## Persistence

None. This build reads the bundled content pack and writes nothing: no settings file, no
save, no log. Versioned settings and saves with atomic replacement are a persistence-phase
deliverable.
