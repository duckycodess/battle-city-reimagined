"""The PEP 561 marker travels with the simulation package.

Nothing in the source imports ``py.typed``, so losing it breaks no other test while
silently untyping the simulation for every package that imports it from an install.
"""

from __future__ import annotations

from importlib import resources


def test_the_package_ships_an_empty_py_typed_marker() -> None:
    marker = resources.files("battle_city_sim").joinpath("py.typed")
    assert marker.is_file(), "the simulation package should contain a py.typed marker"
    assert marker.read_text(encoding="utf-8") == "", (
        "py.typed should be empty: 'partial' would declare the inline types incomplete"
    )
