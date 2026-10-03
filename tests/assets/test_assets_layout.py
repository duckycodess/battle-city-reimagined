"""The packer: the same frames always land in the same rectangles.

The validator's strongest structural check re-runs this function over the sidecar's own
declared sizes and requires the declared rectangles back. That check only means something
while packing is a pure function of names and sizes, so these tests pin exactly that.
"""

from __future__ import annotations

import pytest
from battle_city_tools.assets.layout import ATLAS_WIDTH, pack


def _sizes(count: int, size: tuple[int, int] = (16, 16)) -> dict[str, tuple[int, int]]:
    return {f"frame-{index:02d}": size for index in range(count)}


def test_packing_is_independent_of_the_order_the_frames_arrive_in() -> None:
    sizes = _sizes(9)
    forward = pack(sizes)
    backward = pack(dict(reversed(list(sizes.items()))))
    assert forward == backward


def test_frames_are_laid_out_in_ascending_name_order() -> None:
    layout = pack(_sizes(4))
    assert [placement.name for placement in layout.placements] == sorted(_sizes(4))


def test_a_shelf_wraps_at_the_atlas_width() -> None:
    layout = pack(_sizes(ATLAS_WIDTH // 16 + 1))
    first, wrapped = layout.placements[0], layout.placements[-1]
    assert first.y == 0
    assert wrapped.x == 0
    assert wrapped.y == 16


def test_the_sheet_is_exactly_as_tall_as_the_shelves_need() -> None:
    layout = pack(_sizes(ATLAS_WIDTH // 16))
    assert (layout.width, layout.height) == (ATLAS_WIDTH, 16)


def test_no_two_rectangles_overlap() -> None:
    layout = pack(_sizes(44))
    seen: set[tuple[int, int]] = set()
    for placement in layout.placements:
        for y in range(placement.y, placement.y + placement.height):
            for x in range(placement.x, placement.x + placement.width):
                assert (x, y) not in seen
                seen.add((x, y))


def test_padding_pushes_frames_apart_without_changing_their_order() -> None:
    layout = pack(_sizes(3), padding=2)
    assert [placement.x for placement in layout.placements] == [0, 18, 36]


def test_a_frame_wider_than_the_atlas_is_refused() -> None:
    with pytest.raises(ValueError, match="wider than"):
        pack({"huge": (ATLAS_WIDTH + 1, 16)})


def test_an_empty_pack_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one frame"):
        pack({})


def test_a_non_positive_frame_is_refused() -> None:
    with pytest.raises(ValueError, match="non-positive size"):
        pack({"bad": (0, 16)})


def test_negative_padding_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        pack(_sizes(2), padding=-1)
