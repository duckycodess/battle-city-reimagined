# Installing, launching and upgrading

Version 0.1.0. Nothing is published, so every route below starts from a checkout. If a
package index ever answers to `battle-city-sim` or any of its siblings, it is not this
project — see [`audit.md`](audit.md#nothing-is-published).

## What you need

* **CPython 3.14 or newer.** 3.14 is the only version this project runs; see
  [`README.md`](README.md#the-configuration-this-project-verifies).
* **[uv](https://docs.astral.sh/uv/).** Every command here uses it. The last verified
  version is 0.11.20.
* A C library situation pygame-ce has a wheel for: it ships cp314 wheels for
  manylinux2014 (x86-64, aarch64, i686), macOS 10.15+ universal2, and Windows (x86,
  x86-64, arm64). On anything else pip falls back to building pygame-ce from source,
  which needs SDL development headers and is not covered here.

Nothing needs a GPU, a display or a network connection at runtime. The client needs a
display *to open a window*, but see [Headless](#headless) below.

## Working on the project

If you are changing the code rather than using it, use the workspace. Every package is
installed as an editable checkout, which is the right thing for development and the wrong
thing for checking a release.

```sh
git clone https://github.com/duckycodess/battle-city-reimagined
cd battle-city-reimagined
uv sync --locked --all-packages --all-groups
make ci
uv run battle-city-client
```

`--locked` fails rather than silently updating `uv.lock`. That file is owned by the
integration issue; see [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Installing a built release

This is the route to use when you want the game rather than the repository, and the one
to use when checking that the distributions are correct.

### 1. Build

```sh
cd battle-city-reimagined
uv build --all-packages
```

Fourteen files land in `dist/`: a wheel and a source distribution for each of the seven
packages. They are reproducible — see [`audit.md`](audit.md#reproducibility), including
what that claim does and does not cover.

### 2. Install into an environment of its own

```sh
uv venv --python 3.14 ~/battle-city
uv pip install --python ~/battle-city --no-index --no-deps \
  dist/battle_city_sim-0.1.0-py3-none-any.whl \
  dist/battle_city_content-0.1.0-py3-none-any.whl \
  dist/battle_city_protocol-0.1.0-py3-none-any.whl \
  dist/battle_city_ai-0.1.0-py3-none-any.whl \
  dist/battle_city_server-0.1.0-py3-none-any.whl \
  dist/battle_city_client-0.1.0-py3-none-any.whl \
  dist/battle_city_tools-0.1.0-py3-none-any.whl
uv pip install --python ~/battle-city --no-deps 'pygame-ce==2.5.8'
```

Two things about that `--no-index --no-deps` pair, because they look like noise and are
not. **`--no-index` is a safety property, not an optimisation**: no `battle-city-*`
project is published, so a resolver asked to find one by name would go looking on PyPI
and whatever it found would be someone else's code. Installing by path means the only way
to get these packages is to have built them. **`--no-deps` is what keeps the names from
being looked up**: the project packages depend on each other, and a resolver handed
`battle-city-client` would try to fetch `battle-city-sim` from an index rather than use
the wheel beside it. Install all seven together and there is nothing left to resolve.

pygame-ce is the one dependency that genuinely comes from an index. To pin it to the
artifact digests `uv.lock` already records, use
[the appendix](#appendix-the-isolated-install-the-release-job-performs).

Afterwards the environment should hold exactly eight distributions:

```sh
uv pip list --python ~/battle-city
```

### 3. Launch

```sh
~/battle-city/bin/battle-city-client              # play a bundled stage
~/battle-city/bin/battle-city-client --pack gimmick-demo
~/battle-city/bin/battle-city-editor --level my-stage.json --output my-stage.json
~/battle-city/bin/battle-city-tools validate --bundled
~/battle-city/bin/battle-city-tools inspect --bundled
```

Each command has a `python -m` equivalent, listed in
[`README.md`](README.md#what-the-release-is). On Windows the commands are in
`Scripts\` rather than `bin/`.

`battle-city-tools validate --bundled` is the quickest proof that the content wheel
carried its own data: it loads the pack, the three classic levels and the schemas out of
`site-packages`. It prints `ok: pack classic 1.0.0, 3 levels`.

### Running the server

There is no server command, by design — see
[`README.md`](README.md#two-things-the-release-deliberately-does-not-contain). Embed the
library:

```python
import battle_city_server
from battle_city_content import load_bundled_pack, load_level, bundled_content_root
from battle_city_server.content import content_ref_for, stage_from_level

pack = load_bundled_pack()
level = load_level(bundled_content_root() / "levels" / "classic-01.json")
lobby_level = battle_city_server.LobbyLevel(
    level_id=level.level_id,
    stage=stage_from_level(level),
    content=content_ref_for(pack, level),
)
```

From there `battle_city_server.LobbyConfig` and `Lobby` describe a session and
`battle_city_server.tcp` adapts it to a socket. The server package's own docstrings are
the reference.

### Headless

The client needs a display. On a machine without one, SDL's dummy driver lets it run
without opening a window, which is how the project's tests and the captures in
[`screenshots/`](screenshots/) are produced:

```sh
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy ~/battle-city/bin/battle-city-client --help
```

That is a way to run the code, not a way to play. Nothing is displayed.

### The asset command needs a source tree

`battle-city-assets` validates the sprite pack under `assets/sprites/starter/`, which no
distribution carries. Its `--pack` default is **resolved against the working directory**,
so the bare command only works from a repository root:

```sh
cd battle-city-reimagined
~/battle-city/bin/battle-city-assets validate                      # uses ./assets/sprites/starter
~/battle-city/bin/battle-city-assets validate --pack path/to/pack  # anywhere
```

Run from anywhere else without `--pack`, it reports `assets/sprites/starter/atlas.json:
the sidecar is missing` and exits 1. That is the tool being specific rather than the tool
being broken, but pass `--pack` and the message is about the pack you meant.

## Upgrading

Versions are not yet distinguishable: every package is `0.1.0` and will stay there until
a release is cut, so a resolver cannot tell an old build from a new one. **Replace the
environment rather than upgrading in place.**

```sh
rm -rf ~/battle-city
# then repeat steps 1 to 3
```

Upgrading in place with `--reinstall` would also work, but a fresh environment is the
only way to notice a package that has been *removed* from the release, and it costs
seconds.

### What survives an upgrade

The client's saved data lives outside the environment and is not touched by installing,
removing or replacing it. Settings, campaign progress and cosmetics go under
`battle-city-reimagined` in the first of these that is set:

1. `$BATTLE_CITY_SAVE_DIR` — used as given, with no suffix appended
2. `$XDG_DATA_HOME`
3. `%APPDATA%`
4. `~/.local/share`

Nothing is created by a launch that changes nothing. Each document carries a schema
version and is migrated forward on read; a save written by a *newer* build, or one that
cannot be decoded, is kept and backed up rather than replaced, and the client says so on
screen and plays on with defaults. A save file is never a reason to refuse to start.

Removing the environment therefore does **not** reset your settings. Deleting that
directory does, and is the recovery step for a save the client will not use.

## Recovery

| Symptom | What it means | What to do |
| --- | --- | --- |
| `battle_city_client: no usable display: ...` (exit 2) | SDL found no display. | Use a desktop session, or `SDL_VIDEODRIVER=dummy` to run headless. |
| `ModuleNotFoundError: No module named 'battle_city_sim'` | The seven wheels were not all installed, most likely `--no-deps` with only some of them named. | Reinstall, naming all seven. |
| `uv pip install` tries to download `battle-city-sim` | `--no-deps` was omitted. | Add `--no-index --no-deps`. **Do not install whatever an index offers under that name.** |
| `battle-city-tools: <file>: <field> ...` (exit 1) | A content document is invalid; the message names the file and the field. | Fix the document. |
| `battle-city-tools: refused: ...` (exit 3) | The tool declined to overwrite something or to write into the bundled pack. | Choose another destination; do not re-run with more force. |
| `assets/sprites/starter/atlas.json: the sidecar is missing` | `battle-city-assets` was run outside a repository root. | `cd` to the checkout, or pass `--pack`. |
| `make ci` fails on a clean checkout | Usually a stale environment. | `rm -rf .venv && uv sync --locked --all-packages --all-groups`. |
| The client starts but the settings look wrong | A save was migrated. | The data directory can be deleted to start over; see above. |

Exit statuses are shared by both tools: `0` success, `1` an invalid document, `2`
argparse's usage error (and the client's "no display"), `3` a refusal.

## Appendix: the isolated install the release job performs

The install in step 2 is the ordinary one. The one CI performs is stricter in a way worth
copying when checking a build, because it resolves *nothing at all* — pygame-ce included:

```sh
uv venv --python 3.14 /tmp/release-env
VENV_PY=/tmp/release-env/bin/python

# pygame-ce, pinned to the version and digests uv.lock records.
"$VENV_PY" - > /tmp/pygame-ce.txt <<'PY'
import sys, tomllib
from pathlib import Path

NAME = "pygame-ce"
lock = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
locked = [entry for entry in lock["package"] if entry["name"] == NAME]
if len(locked) != 1:
    sys.exit(f"uv.lock holds {len(locked)} entries for {NAME}, expected exactly one")
package = locked[0]
digests = sorted(
    {artifact["hash"] for artifact in package.get("wheels", [])}
    | ({package["sdist"]["hash"]} if "sdist" in package else set())
)
lines = [f"{NAME}=={package['version']} \\"]
lines += [f"    --hash={digest} \\" for digest in digests[:-1]]
lines += [f"    --hash={digests[-1]}"]
print("\n".join(lines))
PY

uv pip install --python "$VENV_PY" --no-index --no-deps \
  dist/battle_city_sim-0.1.0-py3-none-any.whl \
  dist/battle_city_content-0.1.0-py3-none-any.whl \
  dist/battle_city_protocol-0.1.0-py3-none-any.whl \
  dist/battle_city_ai-0.1.0-py3-none-any.whl \
  dist/battle_city_server-0.1.0-py3-none-any.whl \
  dist/battle_city_client-0.1.0-py3-none-any.whl \
  dist/battle_city_tools-0.1.0-py3-none-any.whl
uv pip install --python "$VENV_PY" --no-deps --require-hashes --requirements /tmp/pygame-ce.txt
```

The digests are read out of `uv.lock` rather than written here, so the lockfile stays the
only place a version or a digest is recorded and the two cannot drift apart. All of
pygame-ce's cp314 and cp315 artifact digests are offered, and `--require-hashes` accepts
whichever one your platform actually downloads.

Then run the installed packages from a directory outside the checkout — in a workspace,
an import that fell back to `packages/*/src` would otherwise pass:

```sh
cd /tmp
SDL_VIDEODRIVER=dummy /tmp/release-env/bin/battle-city-client --help
/tmp/release-env/bin/battle-city-tools validate --bundled
```
