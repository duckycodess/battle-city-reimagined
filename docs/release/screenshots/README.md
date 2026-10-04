# Release evidence captures

Two frames drawn by the **installed** client wheel, reading the **installed** content
wheel, in the clean environment described in [`../installing.md`](../installing.md). They
are here as evidence that the distributions this project builds can be installed and can
render a classic stage; they are not art references and not a UI record. The client's own
screenshots live under `tests/client/screenshots/` and are refreshed by the client's
tests.

| File | What it is |
| --- | --- |
| `01-offscreen-main-menu.png` | The shell's main menu, over the bundled stage catalogue read out of `site-packages`. |
| `02-offscreen-classic-01.png` | Bundled `classic-01` after twelve ticks: terrain, the player tank at its declared spawn, the base, and the HUD. |

## These are offscreen captures, not desktop screenshots

They were taken with SDL's `dummy` video driver. SDL draws into memory; no window is
created, no window manager and no compositor are involved, and nothing was displayed to
anyone. That is what lets them be taken on a headless machine and in continuous
integration, and it is also the limit of what they prove.

**A capture like this shows what the renderer produced. It cannot show that a real window
opened on a real desktop.** Window creation, the resize path, vsync, the desktop's scale
and the audio device are all untested by these two files. The machine that produced them
had no display at all:

| | |
| --- | --- |
| Captured | 2026-10-04 |
| Host | Linux 6.6 (WSL2) on x86-64, **no `DISPLAY`, no Wayland socket** |
| Python | CPython 3.14.4 |
| pygame-ce | 2.5.8 (SDL 2.32.10) |
| Video / audio driver | `SDL_VIDEODRIVER=dummy`, `SDL_AUDIODRIVER=dummy` |
| Source | the seven wheels built from this branch, installed per `../installing.md` |

A windowed desktop capture is an open gap, recorded as such in
[`../audit.md`](../audit.md). Producing one needs a machine with a display, which is not
available here; nothing in this directory should be read as standing in for it.

## Taking them again

From the clean environment, with the repository checked out only so that the script can
be read from here. The script imports nothing from the checkout.

```sh
cd "$(mktemp -d)"          # anywhere outside the checkout
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy /path/to/release-env/bin/python - . <<'PY'
import sys
from pathlib import Path

import pygame
from battle_city_client import (
    ClientShell, ProceduralAssetLibrary, Renderer, StageSession,
    bundled_stage_catalog, stage_from_level, theme,
)
from battle_city_content import bundled_content_root, load_level
from battle_city_sim import DEFAULT_RULES

SCALE = 2
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
pygame.display.init()
renderer = Renderer(ProceduralAssetLibrary(DEFAULT_RULES))


def write(shell: ClientShell, name: str) -> None:
    surface = pygame.Surface(theme.LOGICAL_SIZE)
    renderer.render(surface, shell)
    scaled = pygame.transform.scale(
        surface, (theme.LOGICAL_SIZE[0] * SCALE, theme.LOGICAL_SIZE[1] * SCALE)
    )
    pygame.image.save(scaled, str(out / name))


catalog = bundled_stage_catalog()
write(ClientShell(catalog=catalog), "01-offscreen-main-menu.png")

level = load_level(bundled_content_root() / "levels" / "classic-01.json")
session = StageSession.start(stage_from_level(level), seed=1).advance(12)
shell = ClientShell(catalog=catalog)
shell.open_session(session)
write(shell, "02-offscreen-classic-01.png")

pygame.display.quit()
pygame.quit()
PY
```

The two captures are not expected to be byte-stable across pygame-ce or SDL versions, and
nothing checks them. They are a record of one run, dated above.
