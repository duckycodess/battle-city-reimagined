"""The accessibility model must be usable without a display, and must prove it alone.

Two claims, and they need different evidence.

*This process* cannot show that importing a module pulls in no display library: pytest
imports every selected test module during collection, and the modules beside this one
load pygame on purpose. So the import claim is made in a **subprocess** that imports
nothing else, which is the only place the question has a meaningful answer.

The second claim is about the source rather than the interpreter: the pure modules must
not name pygame at all. A grep would pass on a commented-out import and fail on the word
in a docstring, so the source is parsed and its import statements are read.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from battle_city_client import accessibility, options

PURE_MODULES: tuple[str, ...] = (
    "battle_city_client.accessibility",
    "battle_city_client.options",
    "battle_city_client.intents",
    "battle_city_client.theme",
)
"""Every module the accessibility model is made of. None may reach for a device."""


def _subprocess_import(statement: str) -> subprocess.CompletedProcess[str]:
    """Run ``statement`` in a bare interpreter and report whether pygame came with it."""
    program = (
        "import sys\n"
        f"{statement}\n"
        "loaded = [name for name in sys.modules if name.split('.')[0] == 'pygame']\n"
        "print('PYGAME' if loaded else 'CLEAN')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("module", PURE_MODULES)
def test_a_pure_module_imports_without_pygame(module: str) -> None:
    """Each module stands alone in a fresh interpreter, with no display library behind it."""
    result = _subprocess_import(f"import {module}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "CLEAN", result.stdout


def test_importing_the_client_package_still_does_not_import_pygame() -> None:
    """The package's lazy-export contract survives the modules this phase added.

    ``gamepad`` is pygame-backed and is published like ``Renderer`` and ``ClientApp``:
    resolved on first access. Importing the package to reach the preference model must
    still cost nothing.
    """
    result = _subprocess_import("import battle_city_client")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "CLEAN", result.stdout


def test_the_whole_preference_model_is_usable_in_a_bare_interpreter() -> None:
    """Not just importable: a preference can be built, changed and read with no display."""
    program = (
        "from battle_city_client.accessibility import AccessibilityPreferences\n"
        "from battle_city_client.options import rows, adjust\n"
        "prefs = AccessibilityPreferences()\n"
        "prefs = adjust(prefs, rows(prefs)[0], 1)\n"
        "print(prefs.contrast.value)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "high"


@pytest.mark.parametrize("path", [accessibility.__file__, options.__file__])
def test_a_pure_module_names_no_display_library_in_its_source(path: str) -> None:
    """Read as a syntax tree, so a mention in prose is not mistaken for an import."""
    source = Path(path).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "pygame" not in imported
