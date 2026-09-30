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

## Importing the package does not import pygame

`import battle_city_client` pulls in the adapter, the tick accumulator, the intents, the
session and the shell — none of which touch a device. The names backed by pygame
(`ClientApp`, `Renderer`, `Presenter`, `ProceduralAssetLibrary`, `build_app`, `main`,
`integer_scale`, `present_rect`) are resolved on first access through a module
`__getattr__`. They behave exactly as if they were imported eagerly, and a type checker
sees the real classes, but a tool that only wants `stage_from_level` or
`FixedTickAccumulator` does not pay for SDL.

This is also what keeps `tests/test_bootstrap.py` — which imports every workspace package
in one interpreter — from loading a display library on the simulation's behalf.

## Screen captures

`tests/client/screenshots/` holds one PNG per screen plus a generated `README.md`
recording the driver, scale, versions and seed that produced them. Ordinary test runs
render every screen into a scratch directory and leave the tracked images alone, so
`pytest` and `make ci` never dirty the working tree. Refresh them deliberately after a
visual change:

```sh
BATTLE_CITY_REFRESH_CAPTURES=1   uv run --locked pytest tests/client/test_client_screenshots.py
```

## Known limitation: the simulation's import-purity check

`tests/sim/test_purity.py::test_importing_the_simulation_does_not_pull_in_a_display_or_a_socket`
reads the shared pytest interpreter's `sys.modules` and asserts no `pygame` module is
present. That is a proxy for "importing `battle_city_sim` does not pull in a display",
and it only measures what it claims while nothing else in the run has imported pygame.

The client's own tests do import pygame. `tests/client/conftest.py` shuts pygame down and
drops its modules once no client test remains — after the last one, or at the end of
collection when a `-k` filter has deselected them all. That restores the precondition,
and it weakens nothing: the substantive assertions in that file read the simulation's
source with `ast` and are unaffected by anything happening in this process.

Two things were fixed rather than papered over. Importing `battle_city_client` no longer
imports pygame, so the bootstrap contract is clean on its own. And the release fires
exactly once, decided from the final selected item list, so it cannot strand a later
client test with pygame missing from `sys.modules`.

**Residual risk, stated plainly.** pytest imports every selected test module during
collection, so pygame is in `sys.modules` before the first test of the session runs. A
conftest under `tests/client` can only put it back afterwards. A session that runs the
purity test *before* the last client test still fails it:

```sh
uv run --locked pytest tests/sim tests/client   # fails: purity runs first
```

Nothing inside this issue's allowed files can prevent that, and reordering another
package's tests from this directory would be a worse cure than the disease. Every
invocation the project actually uses passes — `make ci`, `pytest`, `pytest tests`,
`pytest tests/client`, `pytest tests/sim`, `pytest tests/test_bootstrap.py
tests/sim/test_purity.py`, and `-k` selections. The check is only genuinely testable in a
subprocess that imports `battle_city_sim` alone, which means editing `tests/sim`.
Tracked as issue #22.
