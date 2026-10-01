"""The PEP 561 marker travels with the content package.

Nothing in the source imports ``py.typed``, so losing it breaks no other test while
silently untyping the content package for every package that imports it from an install.
The wheel is built from an explicit include list, so the marker is also the kind of file
a manifest edit can drop without any other failure.
"""

from __future__ import annotations

from importlib import resources


def test_the_package_ships_an_empty_py_typed_marker() -> None:
    marker = resources.files("battle_city_content").joinpath("py.typed")
    assert marker.is_file(), "the content package should contain a py.typed marker"
    assert marker.read_text(encoding="utf-8") == "", (
        "py.typed should be empty: 'partial' would declare the inline types incomplete"
    )
