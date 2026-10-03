# battle_city_client

The pygame-ce client: window, input, presentation. It renders simulation state and
translates keystrokes into tick-indexed simulation commands. It owns no game rules.

## Launch it

From the repository root:

```sh
uv run --locked --package battle-city-client python -m battle_city_client
```

Options: `--seed`, `--scale` (1-8), `--frame-cap`. `--help` lists them, and prints the
values a saved profile supplies. `BATTLE_CITY_SAVE_DIR` moves the profile somewhere else. To reach a
lobby, add `--server HOST:PORT --session ID --ticket TICKET`, optionally `--name` and
`--content PACK@VERSION/LEVEL#SCHEMA`. The three online options are required together;
without them the `ONLINE` menu entry says what is missing rather than disappearing.

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
| Resume the saved stage | `R`, on the stage list |
| Choose a badge | Up and down, on the controls screen |
| Lobby ready  | `R`             |
| Lobby mode (host)  | `M`       |
| Lobby stage (host) | `L`       |
| Lobby start (host) | `Enter`   |
| Leave a lobby or an online run | `ESC` |
| Quit         | Close the window, or `QUIT` on the main menu |

Keyboard only in this build. Gamepad support and remapping are accessibility
requirements; they arrive as further tables feeding the same `Action` vocabulary in
`keymap.py`, and nothing downstream of that module knows what a device is.

## What it does and does not simulate

Movement, firing, brick and cracked-brick damage, mirror reflection, water, forest cover
and the home base are all the simulation's, driven at a fixed 60 ticks per second
independently of how fast the window redraws.

The campaign is `battle_city_client.campaign`, and it is pygame-free: enemy quota per
stage, one enemy on tick 0 and every 600 ticks after, score summed from the simulation's
own `ScoreAwarded` events, lives carried between stages, the stage-clear condition, a
campaign that can be completed, and what a restart rewinds. The values are the historical
runtime's; the two places v1 departs from it — a reachable win, and a score cleared on
restart — are argued in `openspec/changes/campaign-v1/design.md` and recorded in the
product specification.

**The enemies this build spawns hold position and never fire.** That is a real limitation
and not a placeholder. Steering is `battle_city_ai`'s, the architecture specification
allows `client → sim, content, protocol` and not `client → ai`, and this package's
manifest declares no dependency on the AI package — so the campaign takes an injected
`EnemyCommandDriver` and the client injects `IdleEnemyDriver`, which commands nobody.
`tests/campaign` injects a driver backed by the AI package, which is how the seam is shown
to carry a real bot. Wiring one into the shipped client is issue #35.

A stage is therefore cleared by destroying its quota, the campaign can be completed, and
the HUD's score and lives are real. What cannot happen in this build is losing: both
failure outcomes need a hostile projectile, a friendly shot at the base is absorbed like
stone, and nothing on the board fires at the player. The terminal screen is still
implemented and still tested — `tests/client` assembles a finished state and renders it,
and `tests/campaign` drives a real loss with an AI-backed driver. Every capture taken from
an assembled state is labelled a test fixture. The client itself never sets an outcome; it
only reads one.

## Campaign and free play

There is one mode. Choosing a stage starts the campaign *at that stage*, with the
starting lives and a score of zero. That is the recorded checkpoint rule and it is
unchanged by saving: every stage in the pack is selectable and every one of them starts
fresh. Pausing offers `RESTART STAGE`, which replays the stage from the score and lives it
began with.

What saving adds is one more way in, asked for explicitly: `R` on the stage list resumes
the saved stage, with the score and the lives *that stage* opened on. Those are the same
two anchors `RESTART STAGE` rewinds to, so a resumed stage is indistinguishable from a
replayed one and no rule about score, lives or pacing is decided differently because a
save exists. The screen shown when a run stops — a stage cleared, the campaign
completed, or a loss — is one overlay whose wording and menu come from the campaign phase.
Giving a stage clear and a campaign completion their own `Screen` members is issue #36;
`tests/client` asserts the current member list and is outside the campaign issue's files.

## Online play

`ONLINE` opens a server-owned lobby: the roster, the agreed mode and stage, who is
ready, and whether the server says the match may start. The host chooses the mode and
the stage; everyone agrees to a specific settings revision, and changing the settings
withdraws every agreement. The server decides all of it — this client composes a host's
message only when it *is* the host, and the server refuses it regardless if it is not.

**An online client runs no simulation.** There is no `step` call anywhere on the online
path. `online.py` holds the session, `remote.py` reads the authoritative snapshot into
something the renderer can draw, and `netlink.py` moves frames on a background thread so
the frame loop never waits on a socket. What is drawn is the last snapshot the server
sent; the run ends when the server says it ended, and a lost link ends the session
without inventing an outcome to fill the gap.

**Competitive modes are selectable and are not playable.** Free-for-all and team battle
are real, versioned settings, carried with their team assignments in the match settings
a session and a replay record. This build will not start one, and says so in the lobby
before anyone presses start: the shared simulation has a single player faction, player
projectiles pass through player tanks, and the only outcomes it records are
`BASE_DESTROYED` and `PLAYERS_ELIMINATED`. Player-versus-player combat, scoring and a
competitive result are simulation rules that belong to an accepted gameplay proposal.
The lobby refuses with `mode_unsupported` rather than running a co-op match and calling
it a duel.

**An online match is not the campaign.** The campaign above — the enemy quota, the
600-tick spawn cadence, the score, the lives carried between stages, stage clear and
campaign completion — is `battle_city_client.campaign`, and it runs in this process for a
local run only. A server runs the shared simulation on the stage the lobby agreed to and
nothing else: no waves are released, no score is kept, and the lives in the HUD are the
simulation's per-slot count rather than a campaign's. The networking specification makes
the server authoritative for score, lives and wave spawning, so those belong on the server
before an online run can have them; putting the client's campaign in charge of a match it
does not own would be exactly the client-side authority the specification forbids. Until
that lands, an online co-op match is two players on one agreed stage, and the lobby does
not claim otherwise.

Reconnect, host migration and a lobby browser are not here. A dropped connection is a
dropped player, a host that leaves ends its lobby, and a lost server is a lost session,
as the networking specification's first release allows.

## Replacing the art

Every pixel the renderer draws comes from an `AssetLibrary` (see `assets.py`).
`ProceduralAssetLibrary` draws stand-ins from primitives at exactly the sizes the
simulation's rules define. The art-pipeline specification calls for Blender-rendered
spritesheets with explicit frame sizes and pivots; shipping those means implementing the
same protocol over an atlas. The renderer, the layout and the simulation do not change,
and art still cannot change collision geometry -- the renderer positions everything from
the body the simulation collides with.

## Persistence

Two small JSON files, written only when something changes. `battle_city_client.persistence`
holds all of it; its module docstring is the reference and this is the summary.

```
settings.json   window scale, frame cap, roster name
campaign.json   stage checkpoint, progression tally, chosen badge
```

Each file is one self-describing envelope — `format`, `kind`, `schema_version`, `data` —
decoded through the protocol's hardened JSON reader, bounded before it is parsed, and
rejected rather than half-applied if it carries a field this build does not know.

They live in `$BATTLE_CITY_SAVE_DIR` if that is set, otherwise
`$XDG_DATA_HOME/battle-city-reimagined`, `%APPDATA%\battle-city-reimagined` or
`~/.local/share/battle-city-reimagined`. The path is resolved on first use and the
directory is created by the first write, so a launch that changes nothing creates nothing.

Writes are atomic: a temporary file, flushed and `fsync`-ed, moved onto the target with
`os.replace`, then a `fsync` of the directory. A reader sees the whole old file or the
whole new one.

**A save file never stops a launch.** A file that cannot be read leaves the client on its
defaults, says so once on the main menu and the controls screen, and is then left exactly
as it is for the rest of the session — a corrupt file because it is the only copy of
whatever it was, and a file from a newer build because it is not damaged at all. An older
file is migrated, and the original is copied to `<name>.v<version>.bak` *before* the
upgraded document replaces it; if that backup cannot be written the upgrade is abandoned
rather than performed without a way back. An existing backup is never written over —
later ones take `.bak.2` through `.bak.9`, each created exclusively, and when every
ordinal is taken the upgrade is refused instead. Recovery does not repair a file, does not
clear old backups, and cannot tell a deleted save from a first launch.

**No secret is ever written.** The settings record has three fields and the server
address, the session identifier and the lobby ticket are not among them; they are launch
options and they stay on the command line.

**A checkpoint is a stage boundary, not a saved game.** It holds the level identifier, its
position, the score and lives that stage opened on — the anchors the campaign already
keeps for restarting a stage — the campaign seed, and a digest of the stage's content.
There is no tick, no tank, no terrain damage and no generator position in a save: a
mid-stage save would be a second source of truth for simulation state, and the canonical
encoding that would have to version it is reserved to an accepted proposal.

**A resume is the same campaign, not a similar one.** Every random stream a stage draws
from is derived from the campaign seed, so resuming uses the seed the run was started
with rather than whatever `--seed` this launch was given. And a level identifier names a
stage without identifying one — the same name lives in another pack, and a pack is edited
in place — so the checkpoint carries `stage_identity`, a digest over the stage's tick-zero
state plus its enemy spawns and declared waves. A save whose level is missing, or whose
stage data has moved under it, is refused with a reason on the stage list and **kept**:
putting the pack back makes it usable again.

**Nothing here is on by default outside a real launch.** `ClientShell` and `build_app`
keep settings and progress in memory with no file behind them; only `main` attaches a
store. Every test in the repository, and every embedding of the shell, is as free of I/O
as it was before.

## Cosmetics

Three badges — `RECRUIT`, `VETERAN` after a stage is cleared, `ACE` after a campaign is
completed — chosen with up and down on the controls screen. Unlocks are *derived* from the
progression tally every time they are read and are never stored, so a hand-edited file can
name a badge but cannot grant one: a selection the tally does not support falls back to
the default.

A badge is a word on the local HUD of the machine that earned it and nothing else. It
reaches no simulation state, no protocol message and no online screen, which is what the
product specification's rule — cosmetics must not alter competitive balance, and
competitive cosmetics must not affect simulation state or visibility — amounts to here.
`tests/persistence` asserts it in all three directions: two profiles with different badges
produce equal per-tick state hashes, byte-identical per-tick `encode_state` output, and
byte-identical outgoing messages across a whole online session.

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

`tests/client/screenshots/` holds one PNG per offline screen plus a generated
`README.md` recording the driver, scale, versions and seed that produced them.
`tests/persistence/screenshots/` holds the screens persistence added — the options and
badge panel, the stage list beside a saved stage, a HUD with a badge on it, and the main
menu after a save file that could not be read.
`tests/multiplayer/screenshots/` holds the online ones — the lobby, a blocked
competitive configuration and a live co-op run — rendered against a real server by
`tests/multiplayer/screenshot_tool.py`, which pytest does not collect. Ordinary test runs
render every screen into a scratch directory and leave the tracked images alone, so
`pytest` and `make ci` never dirty the working tree. Refresh them deliberately after a
visual change:

```sh
BATTLE_CITY_REFRESH_CAPTURES=1   uv run --locked pytest tests/client/test_client_screenshots.py
BATTLE_CITY_REFRESH_CAPTURES=1   uv run --locked pytest tests/persistence/test_persistence_screenshots.py
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

## The level editor

`battle_city_client.editor` is a separate program in the same package: a 16x16 grid
editor over the declarative content format, with its own logical frame, its own window
and no simulation at all.

```sh
uv run --locked --package battle-city-client python -m battle_city_client.editor \
  --level packages/content/src/battle_city_content/levels/classic-01.json \
  --output /tmp/my-level.json
```

| Action                      | Input                     |
| --------------------------- | ------------------------- |
| Apply the tool to a cell    | Left click, or drag       |
| Select a tile               | Click a swatch, or `0`-`8` |
| Paint / player / enemy / delete | `B` / `P` / `E` / `X` |
| Next player slot            | `TAB`                     |
| Validate                    | `V`                       |
| Save to `--output`          | `S`                       |
| Re-read the opened file     | `R`                       |
| Window scale                | `-` and `+`               |
| Quit                        | `ESC`; an edited document asks twice |

`--output` is what makes a save possible and it is never inferred: an editor that wrote
back over whatever it opened would be one keystroke from destroying a bundled stage. An
existing target also needs `--overwrite`, and the packaged content root is refused either
way. Nothing is written until the document has passed `battle_city_content.load_level`,
and the write is atomic.

### Why it does not use `battle_city_tools`

The architecture specification allows `client → sim, content, protocol`; it does not
allow `client → tools`, and the client manifest declares no such dependency. The editor
therefore keeps a small document model of its own in `editor/document.py` instead of
importing `battle_city_tools.LevelDraft`, and that duplication is written down there
rather than hidden. It is bounded: both models hold the same five things, and neither
copies a *validation rule* — both ask the content loader. The editor, the headless tools
and the game cannot disagree about what a valid level is.

### Tests and captures

The editor's tests live under `tests/tools`, beside the headless tools they are the
counterpart to, because this issue does not own `tests/client`. That directory
deliberately has no `conftest.py` and imports pygame only inside the functions that need
it; `tests/tools/tools_helpers.py` records why. Screen captures are refreshed the same
way the client's are:

```sh
BATTLE_CITY_REFRESH_CAPTURES=1 uv run --locked pytest tests/tools/test_editor_screenshots.py
```

`tests/tools` has no `conftest.py`, because the repository's `mypy packages tests`
rejects a second module by that name and `tests/client` already has one. It still puts
the interpreter back: `tools_helpers.pygame_module_boundary` is a module-scoped autouse
fixture that each pygame-using test module imports by name, and it shuts pygame down and
drops it — together with the client modules that import it — once that module's last test
has run. Releasing per module rather than once at the end makes it independent of
collection order and of any `-k` filter, so no ordering carries a loaded display library
into `tests/sim/test_purity.py`:

```sh
uv run --locked pytest tests/tools tests/sim   # passes
uv run --locked pytest tests/sim tests/tools   # passes
```

Collection is clean on its own: nothing under `tests/tools` imports pygame until a test
asks for it.
