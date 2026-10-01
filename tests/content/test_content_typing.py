"""The PEP 561 marker travels with the content package.

Nothing in the source imports ``py.typed``, so losing it breaks no other test while
silently untyping the content package for every package that imports it from an install.
This reads the marker through the import system, so it guards the file in the source
tree, not its presence in any built artifact; packaging is a separate concern, checked
at build time against the manifest its own shared-contract issue owns.
"""

from __future__ import annotations

from importlib import resources


def test_the_package_ships_an_empty_py_typed_marker() -> None:
    marker = resources.files("battle_city_content").joinpath("py.typed")
    assert marker.is_file(), "the content package should contain a py.typed marker"
    assert marker.read_text(encoding="utf-8") == "", (
        "py.typed should be empty: 'partial' would declare the inline types incomplete"
    )
