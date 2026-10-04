# Release audit

Provenance, dependencies, licensing and the gaps, for version 0.1.0 as built on
2026-10-04. Everything below is either verified in this repository or named as
unverified. Where a claim is weaker than it looks, it says so.

## Nothing is published

No package index entry, no tag, no GitHub release, no binary anywhere. The only way to
obtain these packages is to build them from a checkout.

That matters beyond tidiness: **the seven distribution names are unclaimed.** If a public
index ever answers to `battle-city-sim`, `battle-city-client` or any of the others, it is
not this project, and installing it would install a stranger's code under a name this
repository uses. This is why the documented install passes explicit wheel paths under
`--no-index --no-deps`, and why the release job's clean install does the same. Name
squatting on an index is a real risk for an unpublished project with memorable package
names; it is recorded here as a risk, not solved, because publishing is out of scope.

## Provenance

### Code

Written for this rebuild. The historical project,
[duckycodess/Battle-City](https://github.com/duckycodess/Battle-City) at revision
`5c9d81cd0de89a05f5946448d19c40fb343b0a2d`, has **no detected licence file**. No Pyxel
runtime code and no module from it is reused; see
[`../historical-source.md`](../historical-source.md).

### Level data

The three classic stages are conversions of the historical stage layouts and spawn
points into this project's JSON. Each bundled level records its source, and the pack
manifest records the position:

> `spdx_id: NOASSERTION` — "The three layouts were converted from stage data in the
> historical duckycodess/Battle-City repository at revision 5c9d81c…, which publishes no
> licence. This pack therefore asserts no licence over that layout data and makes no
> claim on the reader's behalf."

`gimmick-demo` is original to the rebuild and is not converted from historical data; it
carries the same `NOASSERTION` so that it does not imply a licence the project has not
chosen.

### Art

The starter sprite pack under `assets/sprites/starter/` is rendered from the Blender
scene scripts in `assets/blender/`. Every mesh, material and light is authored by
`build_scene.py` in this repository. No artwork, texture or sheet from the historical
repository was copied, traced or re-encoded, and no generated image is a runtime
dependency — the art pipeline specification permits generated images as concept
references only, and none were used as a source. Six source records in `atlas.json` name
the scene, the build script, the render script and the collection behind every frame.

The render environment, the pinned Cycles settings and the reproducibility measurements
are recorded in [`../../assets/blender/README.md`](../../assets/blender/README.md), which
is the authority on the art pipeline. It is also honest about its own limits: the
`.blend` file is a derived convenience artifact and is explicitly not reproducible, and
Cycles being bit-exact across two runs on one host does not establish that it is so
across hosts.

### Source-tree art

**No distribution contains the atlas.** It lives at the repository root, outside every
package, and the client renders procedurally rather than reading it — the art pipeline
specification records that the renderer does not consume the Blender atlas. Packaging it
would mean moving it into a package tree or teaching the client to load it, and both are
runtime changes outside this issue.

So it is audited where it is. The release job runs the *installed* tool against the
source tree:

```sh
battle-city-assets validate --pack assets/sprites/starter
```

which re-derives every claim in `atlas.json` from the pixels — the layout, the
per-frame and whole-sheet digests, the palette, the hitboxes against
`battle_city_sim.DEFAULT_RULES`, and the readability thresholds against `catalog.py`. It
reports `19 checks passed`. Those readability thresholds are the pack's own regression
guards measured off the shipped art, **not** a conformance claim against any
accessibility standard; `assets/sprites/starter/README.md` makes that argument at length
and it is not repeated here.

`stage-composite.png` in that directory is a pipeline composite with a banner saying so
in its own pixels. It is not a game capture and must not be presented as one.

## Dependencies

### Runtime

One, for one package.

| Package | Version | Required by | Licence (declared) | Source |
| --- | --- | --- | --- | --- |
| `pygame-ce` | 2.5.8 | `battle-city-client` | LGPL (as classified by the package) | PyPI, pinned in `uv.lock` by sdist and wheel digests |

`battle-city-sim`, `battle-city-content` and `battle-city-protocol` have **no**
dependencies at all, which is the architecture specification's rule for the simulation
holding in the packaging as well as in the imports. `battle-city-ai`,
`battle-city-server` and `battle-city-tools` depend only on project packages.

The LGPL classifier is pygame-ce's own metadata. **This project has not performed an
LGPL compliance review**, and it cannot usefully perform one before choosing its own
licence (below). Dynamic linking against an unmodified upstream wheel is the ordinary
case the LGPL is written for, but "ordinary" is not advice and this note is not legal
advice. It is a release blocker, listed as one.

### Development

Thirteen more packages, none of which ships in any distribution. Versions are from
`uv.lock`; licences are read from the installed distributions' metadata.

| Package | Version | Why | Licence (declared) |
| --- | --- | --- | --- |
| `mypy` | 2.3.1 | type checking | MIT |
| `pytest` | 9.1.1 | tests | MIT |
| `ruff` | 0.16.9 | format and lint | MIT |
| `ast-serialize` | 0.11.2 | via mypy | MIT |
| `colorama` | 0.4.6 | via pytest, **Windows only** | BSD License (classifier; see note) |
| `iniconfig` | 2.3.0 | via pytest | MIT |
| `librt` | 0.16.0 | via mypy | MIT |
| `mypy-extensions` | 1.1.0 | via mypy | MIT |
| `packaging` | 26.3 | via pytest | Apache-2.0 OR BSD-2-Clause |
| `pathspec` | 1.1.1 | via mypy | MPL-2.0 |
| `pluggy` | 1.6.0 | via pytest | MIT |
| `pygments` | 2.21.0 | via pytest | BSD-2-Clause |
| `typing-extensions` | 4.16.0 | via mypy | PSF-2.0 |

Two reservations about this table. The licence column is **what each package declares
about itself**, collected from the installed distributions' metadata — a
`License-Expression`, else a licence classifier, else a `License` field; no licence text
was read and no compatibility analysis was done. Several of these are classifiers rather
than SPDX expressions, which is why `colorama` reads "BSD License" and pygame-ce reads
"LGPL": those are the strings the packages publish, not identifiers this project
resolved. And `colorama` resolves only under a Windows marker, so it is not installed in
the verified configuration; its row was read from a copy installed separately for this
audit.

### Vulnerabilities

| | |
| --- | --- |
| Tool | `pip-audit` 2.10.1, run via `uvx` |
| Input | all 14 third-party packages, exported from `uv.lock` with `uv export --locked --all-packages --all-groups` |
| Date | 2026-10-04 |
| Result | **No known vulnerabilities found** |

```sh
uv export --locked --all-packages --all-groups --no-emit-workspace \
    --format requirements-txt > /tmp/all.txt
uvx --from pip-audit pip-audit --requirement /tmp/all.txt --disable-pip
```

Three limits on that result, none of them rhetorical.

* **It is a point in time.** A clean scan on 2026-10-04 says nothing about 2026-10-05. It
  is a query against an advisory database, not a property of the code.
* **It is not in CI.** `pip-audit` is not in `uv.lock`, and wiring an unpinned tool that
  queries a network service into a required check would make the check both unpinned and
  flaky. Scheduling it is a real improvement and is listed as a follow-up rather than
  done here, because adding a dependency needs a demonstrated need and an owner.
* **It covers Python packages only.** pygame-ce bundles SDL and its codec libraries
  inside its wheel; `pip-audit` sees the wheel, not the C libraries in it. The SDL build
  in the verified configuration is 2.32.10.

## Licensing

**The project has no licence, and this change does not choose one.**

There is no `LICENSE` file in the repository. The seven distributions carry no `License`,
`License-Expression` or `License-File` metadata, and no licence classifier. That is
deliberate on both counts:

* **Choosing a licence is the owner's decision**, not an implementation detail of a
  packaging issue. It is irreversible in practice — anyone who receives a copy under a
  licence keeps it — so it is exactly the kind of choice a bounded issue should leave
  alone.
* **`NOASSERTION` must not be put in package metadata.** The content pack and the atlas
  sidecar use `NOASSERTION` correctly: it is an SPDX *document* value meaning "no
  assertion is being made here", and that is the honest statement about layout data
  converted from an unlicensed source. It is **not** a valid SPDX licence expression, so
  writing `License-Expression: NOASSERTION` into a core-metadata field would be invalid
  metadata that reads, to a tool, like a licence identifier. Absence is the accurate
  encoding of "undecided"; a wrong identifier is not.

What this means for a reader: **nothing here grants you any licence.** Without one,
default copyright applies to the rebuilt code and assets, and no licence is asserted over
the converted layout data either. Nobody may rely on being permitted to redistribute this
project. That is the status quo this change preserves rather than creates — it is the
first release blocker below, and resolving it needs the owner.

## Reproducibility

`uv build --all-packages` is byte-reproducible in the verified configuration. Measured
rather than assumed:

* Two consecutive builds from the same tree produced identical bytes for all fourteen
  files.
* A **fresh clone at a different path, with every source file's mtime forced to a
  different date**, produced identical bytes for all fourteen files. That is the check
  that matters, because it is the one a same-tree rebuild cannot fail.

The digests of that build, for the record:

| File | SHA-256 |
| --- | --- |
| `battle_city_ai-0.1.0-py3-none-any.whl` | `5ccc542ea0be5f5622e5bd02f0523fb09b41061a6ab18ab9af301a2f1f9fb74a` |
| `battle_city_ai-0.1.0.tar.gz` | `8bfaca8c4a7dbf1d515f05d70ef1bbb75ad23a605c452487b9e50524e515c76c` |
| `battle_city_client-0.1.0-py3-none-any.whl` | `28345dc220d7aa4c07591d2b6d303b27d110cb60ac57d483ebf759115af6b9d4` |
| `battle_city_client-0.1.0.tar.gz` | `9af2f5e883ab436e80ecbbdc043f27b9a5f362e068654a7838c6e39124fe1806` |
| `battle_city_content-0.1.0-py3-none-any.whl` | `86932f9e2d9b848a5d452dcc230c49b19f2d391af6abd8f39a3f29d91d04ce51` |
| `battle_city_content-0.1.0.tar.gz` | `16051bb534bf0c54fb03ed74bb4baec15a197918714d5634f0daa33b952a4564` |
| `battle_city_protocol-0.1.0-py3-none-any.whl` | `245c65f708f8212c6d1e7716f38183c08207380d98fe4a447a3649325d796698` |
| `battle_city_protocol-0.1.0.tar.gz` | `b6c9606a946ae6a7870c6d953689cc20bfcd200b7f60087e4908d13da1766ccb` |
| `battle_city_server-0.1.0-py3-none-any.whl` | `dd34fae9e720a741619eb9d03afef785fb7c6c3c79e1bfc611618510d8a3fbfb` |
| `battle_city_server-0.1.0.tar.gz` | `ea207a7ddbf9098d906b3e67bdbf9e3872b8780eb407fa42f698514d693c526a` |
| `battle_city_sim-0.1.0-py3-none-any.whl` | `ba6556dcda4ad1aca81662b0304fb4671fb845488f892ebeb1258be8d4ee656e` |
| `battle_city_sim-0.1.0.tar.gz` | `a6d108827731e820373cc7bc47bf26b0b94b02b0910f49687437c75c5cb64d02` |
| `battle_city_tools-0.1.0-py3-none-any.whl` | `93f62c270b3e4c9669d733d59cf96eb97436ef03b54b25f0f43c0e880971ddce` |
| `battle_city_tools-0.1.0.tar.gz` | `7d3f948ba3fb4e7dbfec1b57f688adda54a3fdc9d36e479bd4f6a7e972f66ede` |

**What this does not claim.** It is one machine, and more importantly one build backend:
`hatchling` is resolved at build time from `requires = ["hatchling>=1.27"]`, which is
**not** pinned in `uv.lock` — the lockfile covers the workspace's dependencies, not each
package's build requirements. The digests above were produced by hatchling 1.32.4 under
uv 0.11.20 and CPython 3.14.4, and a later hatchling may legitimately produce different
bytes. Nothing in this project depends on these digests; they are a record of one build,
not a contract. Reproducibility across backend versions is unverified and was not
attempted.

| | |
| --- | --- |
| Built | 2026-10-04 |
| Host | Linux 6.6 (WSL2) on x86-64 |
| Python | CPython 3.14.4 |
| uv | 0.11.20 |
| Build backend | hatchling 1.32.4 |

## Platform coverage

| | Verified | Notes |
| --- | --- | --- |
| Linux x86-64, CPython 3.14 | **Yes** | `ubuntu-latest` in CI, and locally on WSL2 |
| Linux aarch64 / i686 | No | pygame-ce publishes cp314 wheels; untried |
| macOS (universal2), CPython 3.14 | No | pygame-ce publishes a cp314 wheel; untried |
| Windows x86-64 / x86 / arm64, CPython 3.14 | No | pygame-ce publishes cp314 wheels; `colorama` resolves here; untried |
| CPython 3.15 | No | the manifests allow `>=3.14` and `uv.lock` resolves for 3.15, but nothing runs it |
| A real desktop window | **No** | see below |

Every wheel is `py3-none-any`; nothing in this project is platform-specific, and the only
platform-sensitive artifact is pygame-ce's own wheel. The expectation that the other rows
work is reasonable. It is still an expectation.

**The windowed desktop case is genuinely untested.** Every capture and every test run
uses SDL's `dummy` driver, so window creation, resizing, vsync, display scaling and the
audio device have never been exercised by anything automated, and the machine this was
built on has no display at all. The captures in [`screenshots/`](screenshots/) say so in
their own README and must not be presented as desktop screenshots.

## Release blockers

Ordered. None is resolvable inside this issue's scope.

1. **No licence.** The owner has to choose one, or decide deliberately not to. Until
   then nobody may redistribute this project and the LGPL question below cannot be
   answered.
2. **No LGPL compliance review for pygame-ce.** Needs a licence first.
3. **The distribution names are unclaimed on PyPI.** Publishing, or defensively
   registering, is an owner decision with credentials attached.
4. **Every package is `0.1.0`** with no release process to move it. A version bump, a
   changelog and a tagging convention all have to exist before an upgrade can mean
   anything to a resolver; today it cannot distinguish two builds.
5. **No windowed desktop verification.** Needs a machine with a display.

## Residual risks

* **One verified platform.** See the table above.
* **The vulnerability scan is a snapshot** and is not in CI.
* **`hatchling` is unpinned** as a build requirement, so the reproducibility measurement
  has a moving part the lockfile does not cover.
* **Three packages ship no `py.typed`.** `battle-city-client`, `battle-city-ai` and
  `battle-city-tools` have no typing marker, so a downstream type checker treats them as
  untyped even though the sources are fully annotated and `mypy --strict` passes over
  them in the workspace. The content package had the same gap until this change, where it
  was a build-filter mistake; for these three the file simply does not exist, and adding
  it would modify package runtime files outside this issue. The release job's archive
  check asserts the four markers that do exist, so this gap cannot widen unnoticed, and
  the three missing files are a small follow-up.
* **Nothing checks the distributions against a published baseline**, because there is no
  published baseline. The archive checks assert against a list written down in the
  workflow; a reviewer should read that list as a specification, because that is what it
  is.
