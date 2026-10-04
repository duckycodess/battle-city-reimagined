# Release

What this project builds, how to check it, and what the result does not yet cover.

| | |
| --- | --- |
| [`installing.md`](installing.md) | Clean install, launch, upgrade, recovery, platform scope. |
| [`audit.md`](audit.md) | Provenance, dependencies, licence position, vulnerability scan, blockers. |
| [`screenshots/`](screenshots/) | Offscreen evidence that the installed wheels render a stage. |

**Nothing here has been published.** No package index, no tag, no GitHub release. These
documents describe building and installing from a checkout, which is the only supported
route today. See [Release blockers](audit.md#release-blockers) for what stands between
that and a published 0.1.0.

## What the release is

Seven distributions, each a wheel and a source distribution, built from the uv workspace:

| Distribution | Import package | What it is |
| --- | --- | --- |
| `battle-city-sim` | `battle_city_sim` | The deterministic headless simulation. No dependencies. |
| `battle-city-content` | `battle_city_content` | Schemas, levels and pack manifests, bundled as package data. No dependencies. |
| `battle-city-protocol` | `battle_city_protocol` | Versioned transport-neutral messages. No dependencies. |
| `battle-city-ai` | `battle_city_ai` | Seeded bot policies. Depends on the simulation. |
| `battle-city-server` | `battle_city_server` | The authoritative asyncio server, **as a library**. |
| `battle-city-client` | `battle_city_client` | The pygame-ce client and the level editor. The only distribution with a third-party dependency. |
| `battle-city-tools` | `battle_city_tools` | Headless content and asset commands. |

Four commands are installed, all of them by the client and the tools:

| Command | Equivalent | Owned by |
| --- | --- | --- |
| `battle-city-client` | `python -m battle_city_client` | `battle-city-client` |
| `battle-city-editor` | `python -m battle_city_client.editor` | `battle-city-client` |
| `battle-city-tools` | `python -m battle_city_tools` | `battle-city-tools` |
| `battle-city-assets` | `python -m battle_city_tools.assets` | `battle-city-tools` |

### Two things the release deliberately does not contain

**The server installs no command.** `battle-city-server` is an importable library: it has
no `main()`, no `__main__` module and no socket-opening entry point of its own. A host
embeds it — `battle_city_server.Lobby`, `GameSession`, the TCP adapter — and drives it.
Giving it a command would mean writing one, which is a runtime change, so it is left to
the issue that designs the deployment shape. `battle_city_server.content.stage_from_level`
is exercised on every bundled level by the clean-install check, so "importable library"
is a tested claim rather than a hopeful one.

**No distribution carries the sprite atlas.** The starter art lives in
`assets/sprites/starter/` at the repository root, outside every package, and the client
draws its own procedural tiles — see the art pipeline specification, which records that
the renderer does not consume the Blender atlas. Packaging the atlas would mean either
moving it into a package or teaching the client to read it, and both are runtime changes.
The art is still audited and validated as a release artifact; it is just validated in the
source tree. See [`audit.md`](audit.md#source-tree-art).

## Checking a build

Everything below also runs in the `release` job of
[`../../.github/workflows/quality.yml`](../../.github/workflows/quality.yml) on every pull
request, and that workflow is the authoritative copy. Run it locally when changing
package metadata.

```sh
uv sync --locked --all-packages --all-groups   # the workspace
make ci                                        # format, lint, types, tests
uv build --all-packages                        # 7 sdists and 7 wheels into dist/
```

`uv build` builds each wheel from the sdist it has just built, so a file missing from an
sdist is missing from the wheel that follows it. The release job then does three things
the commands above cannot:

1. **Reads the archives without installing them.** Data files, typing markers and exactly
   the four console scripts, asserted in the wheel *and* in the sdist, plus
   `Requires-Python`, plus a check that nothing from outside the package tree crept in.
   This has to be done by reading, because in a workspace the file a wheel forgot is
   still on the import path.
2. **Installs the seven wheels into an environment that shares nothing with the
   workspace**, from explicit paths under `--no-index`, with pygame-ce pinned by
   `--require-hashes` to the digests read out of `uv.lock`. Nothing is resolved by name.
3. **Runs them from outside the checkout**, so an import that quietly fell back to
   `packages/*/src` fails instead of passing. The bundled pack is loaded from
   `site-packages`, client and server are required to map the same content to the same
   stage, and thirty ticks are required to hash the same twice.

The clean-install procedure is written out step by step in
[`installing.md`](installing.md#appendix-the-isolated-install-the-release-job-performs).

## The configuration this project verifies

| | |
| --- | --- |
| Python | CPython **3.14** — the manifests declare `>=3.14`; 3.14 is the one version run |
| Operating system | Linux (`ubuntu-latest` in CI; the last local run was WSL2 on x86-64) |
| Architecture | x86-64 |

pygame-ce ships cp314 wheels for macOS, Windows and Linux on several architectures, and
nothing in this project is platform-specific, so other platforms are expected to work.
**Expected is not verified.** [`audit.md`](audit.md#platform-coverage) states the coverage
gap plainly rather than letting a green badge imply more than one row of this table.
