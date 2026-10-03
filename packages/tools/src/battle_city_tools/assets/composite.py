"""A stage picture assembled from a bundled level and the tracked atlas.

Read the banner it draws into itself: this is **not** a screenshot. Nothing here runs the
client, the simulation or pygame. The terrain is a bundled level document read through
:mod:`battle_city_content`, the sprites are the frames in the atlas, and the entities are
placed by the fixed rule below.

It exists because the issue asks to see terrain, both factions, mirror lean, forest
concealment and powerup readability at gameplay scale, and because the only seam that
would produce a real capture is the client's asset library, which this change may not
touch. Shipping a composite and labelling it honestly is the conservative half of that
request; a picture that looked like a capture and was not would be the dishonest half.
When the client grows an atlas-backed asset library, a real capture replaces this.

The placement rule is deterministic and derives from the level document, so the picture
changes only when the level, the atlas or this rule changes:

* terrain comes from the grid, one frame per tile code, water at phase 0
* forest is drawn again after the entities, because it is an overlay that conceals
* the player tank stands on the level's own player spawn, facing up
* the two enemy spawns carry a normal and a shielded enemy
* one unshielded enemy stands on the first forest cell in row-major order, and the same
  sprite stands on the first empty cell beside it, so the canopy can be seen covering one
  and not the other
* a shot sits one pixel short of the first mirror cell, travelling right into it
* the three powerups take the three empty cells nearest the middle of the grid
* an explosion sits on the first brick cell, and the invincibility ring on the player
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from battle_city_content import GridCell, Level, TileCode

from .font import GLYPH_HEIGHT, draw_text, text_width
from .metadata import AtlasMetadata, FrameRecord
from .raster import Canvas, Image, Rgba

SCALE: Final[int] = 2
MARGIN: Final[int] = 10
BANNER_HEIGHT: Final[int] = 22
CAPTION_LEADING: Final[int] = 4

BACKGROUND: Final[Rgba] = (14, 16, 22, 255)
BANNER: Final[Rgba] = (150, 54, 48, 255)
BANNER_TEXT: Final[Rgba] = (255, 255, 255, 255)
CAPTION: Final[Rgba] = (134, 146, 168, 255)
EDGE: Final[Rgba] = (58, 68, 88, 255)

BANNER_LINE: Final[str] = "PIPELINE COMPOSITE - NOT AN IN-GAME CAPTURE"
CAPTION_LINES: Final[tuple[str, ...]] = (
    "Assembled by battle_city_tools.assets.composite from a bundled level document and the",
    "tracked atlas. The client was not run: no pygame, no window, no simulation, no clock.",
    "Entity placement is a fixed rule in composite.py, not gameplay. It is here to show",
    "faction colour, facing, mirror lean, forest concealment and powerup readability at 16 px.",
)

TILE_FRAMES: Final[dict[str, str]] = {
    TileCode.EMPTY.value: "terrain-empty",
    TileCode.STONE.value: "terrain-stone",
    TileCode.BRICK.value: "terrain-brick",
    TileCode.MIRROR_NE.value: "terrain-mirror-ne",
    TileCode.MIRROR_SE.value: "terrain-mirror-se",
    TileCode.WATER.value: "terrain-water-0",
    TileCode.CRACKED_BRICK.value: "terrain-brick-cracked",
    TileCode.FOREST.value: "terrain-forest",
    TileCode.HOME.value: "terrain-home-intact",
}

POWERUP_FRAMES: Final[tuple[str, ...]] = (
    "powerup-gatling",
    "powerup-invincibility",
    "powerup-extra-life",
)


@dataclass(frozen=True, slots=True)
class _Sprite:
    """One frame placed at a playfield pixel, already adjusted for its pivot."""

    frame: str
    x: int
    y: int


def build_composite(metadata: AtlasMetadata, atlas: Image, level: Level) -> Image:
    """Draw ``level`` with the frames in ``atlas``, banner and caption included."""
    frames = {frame.name: frame for frame in metadata.frames}
    tile_size, _ = metadata.frame_size
    field = Canvas(level.grid.width * tile_size, level.grid.height * tile_size, BACKGROUND)

    forest_cells: list[GridCell] = []
    for y, row in enumerate(level.grid.rows):
        for x, code in enumerate(row):
            cell = GridCell(x, y)
            if code == TileCode.FOREST.value:
                forest_cells.append(cell)
                _draw(field, atlas, frames, "terrain-empty", x * tile_size, y * tile_size)
                continue
            _draw(field, atlas, frames, TILE_FRAMES[code], x * tile_size, y * tile_size)

    for sprite in _entities(level, tile_size, forest_cells, metadata):
        _draw(field, atlas, frames, sprite.frame, sprite.x, sprite.y)

    for cell in forest_cells:
        _draw(field, atlas, frames, "terrain-forest", cell.x * tile_size, cell.y * tile_size)

    playfield = field.freeze().scaled(SCALE)
    return _frame_it(playfield, level)


def _entities(
    level: Level, tile_size: int, forest_cells: list[GridCell], metadata: AtlasMetadata
) -> tuple[_Sprite, ...]:
    sprites: list[_Sprite] = []
    spawn = level.player_spawns[0]
    player_x, player_y = spawn.cell.x * tile_size, spawn.cell.y * tile_size
    sprites.append(_Sprite("tank-player-up", player_x, player_y))

    enemy_frames = ("tank-enemy-normal-down", "tank-enemy-shielded-left")
    for cell, frame in zip(level.enemy_spawns, enemy_frames, strict=False):
        sprites.append(_Sprite(frame, cell.x * tile_size, cell.y * tile_size))

    if forest_cells:
        hidden = forest_cells[0]
        sprites.append(
            _Sprite("tank-enemy-unshielded-down", hidden.x * tile_size, hidden.y * tile_size)
        )
        twin = _free_neighbour(level, hidden)
        if twin is not None:
            sprites.append(
                _Sprite("tank-enemy-unshielded-down", twin.x * tile_size, twin.y * tile_size)
            )

    mirror = _first_cell(level, (TileCode.MIRROR_NE, TileCode.MIRROR_SE))
    if mirror is not None:
        shot = metadata.frame("shot-right")
        centre_x = mirror.x * tile_size - 3
        centre_y = mirror.y * tile_size + tile_size // 2
        sprites.append(_Sprite("shot-right", centre_x - shot.pivot[0], centre_y - shot.pivot[1]))

    for frame, cell in zip(POWERUP_FRAMES, _central_empty_cells(level, 3), strict=False):
        sprites.append(_Sprite(frame, cell.x * tile_size, cell.y * tile_size))

    brick = _first_cell(level, (TileCode.BRICK,))
    if brick is not None:
        sprites.append(_Sprite("effect-explosion-2", brick.x * tile_size, brick.y * tile_size))
    sprites.append(_Sprite("effect-shield-0", player_x, player_y))
    return tuple(sprites)


def _free_neighbour(level: Level, cell: GridCell) -> GridCell | None:
    """The first empty neighbour, so the concealed tank has a visible twin to compare with.

    Concealment is only legible as a difference. One tank under a canopy looks like no
    tank at all, which demonstrates nothing; the same sprite standing beside it on open
    ground is what shows the canopy doing its job.
    """
    occupied = {(spawn.cell.x, spawn.cell.y) for spawn in level.player_spawns}
    occupied.update((spawn.x, spawn.y) for spawn in level.enemy_spawns)
    for dx, dy in ((-1, 0), (1, 0), (0, 1), (0, -1)):
        candidate = GridCell(cell.x + dx, cell.y + dy)
        if not level.grid.contains(candidate) or (candidate.x, candidate.y) in occupied:
            continue
        if level.grid.rows[candidate.y][candidate.x] == TileCode.EMPTY.value:
            return candidate
    return None


def _first_cell(level: Level, kinds: tuple[TileCode, ...]) -> GridCell | None:
    wanted = {kind.value for kind in kinds}
    for y, row in enumerate(level.grid.rows):
        for x, code in enumerate(row):
            if code in wanted:
                return GridCell(x, y)
    return None


def _central_empty_cells(level: Level, count: int) -> tuple[GridCell, ...]:
    """Empty cells nearest the middle, ties broken by row then column."""
    occupied = {(spawn.cell.x, spawn.cell.y) for spawn in level.player_spawns}
    occupied.update((cell.x, cell.y) for cell in level.enemy_spawns)
    middle_x, middle_y = level.grid.width // 2, level.grid.height // 2
    candidates: list[tuple[int, int, int]] = []
    for y, row in enumerate(level.grid.rows):
        for x, code in enumerate(row):
            if code != TileCode.EMPTY.value or (x, y) in occupied:
                continue
            candidates.append((abs(x - middle_x) + abs(y - middle_y), y, x))
    candidates.sort()
    return tuple(GridCell(x, y) for _, y, x in candidates[:count])


def _draw(
    canvas: Canvas,
    atlas: Image,
    frames: dict[str, FrameRecord],
    name: str,
    x: int,
    y: int,
) -> None:
    rect = frames[name].rect
    canvas.blit(atlas.crop(rect[0], rect[1], rect[2], rect[3]), x, y)


def _frame_it(playfield: Image, level: Level) -> Image:
    caption_height = (len(CAPTION_LINES) + 1) * (GLYPH_HEIGHT + CAPTION_LEADING)
    width = max(
        playfield.width + MARGIN * 2,
        max(text_width(line) for line in CAPTION_LINES) + MARGIN * 2,
    )
    height = MARGIN + BANNER_HEIGHT + MARGIN + playfield.height + MARGIN + caption_height + MARGIN
    canvas = Canvas(width, height, BACKGROUND)

    canvas.fill_rect(MARGIN, MARGIN, width - MARGIN * 2, BANNER_HEIGHT, BANNER)
    draw_text(
        canvas,
        BANNER_LINE,
        MARGIN + (width - MARGIN * 2 - text_width(BANNER_LINE, scale=2)) // 2,
        MARGIN + (BANNER_HEIGHT - GLYPH_HEIGHT * 2) // 2,
        BANNER_TEXT,
        scale=2,
    )

    top = MARGIN + BANNER_HEIGHT + MARGIN
    left = (width - playfield.width) // 2
    canvas.fill_rect(left - 1, top - 1, playfield.width + 2, playfield.height + 2, EDGE)
    canvas.blit(playfield, left, top)

    caption_top = top + playfield.height + MARGIN
    lines = (f"level {level.level_id}: {level.name}", *CAPTION_LINES)
    for index, line in enumerate(lines):
        draw_text(
            canvas, line, MARGIN, caption_top + index * (GLYPH_HEIGHT + CAPTION_LEADING), CAPTION
        )
    return canvas.freeze()
