"""The simulation boundary: no I/O, no clock, no shared randomness.

The architecture specification forbids the simulation from reading clocks, frames,
sockets, files, the environment or unseeded randomness. A review can miss a new import,
so the boundary is asserted mechanically over the package source.

The runtime half of that claim is measured in a *fresh interpreter*. Reading this
process's :data:`sys.modules` measured the test session rather than the simulation: any
earlier test in the same run that imported pygame -- a client test, an editor test, a
screenshot -- put it there, and the reading then said the simulation had loaded a display
that the simulation never touched. It was a false failure in one test order and, worse,
a vacuous pass in every other, because a simulation that really did import pygame would
have been indistinguishable from a client test that ran first. A subprocess has one
import in it, so the reading is about the import and nothing else.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import battle_city_sim
import pytest

ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "hashlib",
        "typing",
    }
)
"""Standard library modules the simulation may use.

``hashlib`` is a pure computation over bytes already in memory and is what backs the
canonical state hash. Everything else here is type and data plumbing.
"""

FORBIDDEN_NAMES = (
    "random",
    "time",
    "datetime",
    "os",
    "sys",
    "pathlib",
    "socket",
    "asyncio",
    "json",
    "pygame",
    "pyxel",
    "threading",
    "subprocess",
    "secrets",
    "uuid",
)

PACKAGE_ROOT = Path(battle_city_sim.__file__).resolve().parent


def _modules() -> tuple[Path, ...]:
    found = tuple(sorted(PACKAGE_ROOT.glob("*.py")))
    assert found, "the simulation package should contain modules"
    return found


def _absolute_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_package_imports_only_allowed_standard_library_modules() -> None:
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        offenders = _absolute_imports(tree) - ALLOWED_IMPORTS
        assert not offenders, f"{module.name} imports {sorted(offenders)}"


def test_the_package_never_imports_a_forbidden_module() -> None:
    for module in _modules():
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module))
        imported = _absolute_imports(tree)
        for name in FORBIDDEN_NAMES:
            assert name not in imported, f"{module.name} imports {name}"


def test_the_package_depends_on_no_other_project_package() -> None:
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for name in _absolute_imports(tree):
            assert not name.startswith("battle_city_"), (
                f"{module.name} imports the project package {name}"
            )


PROBE = """\
import sys

import battle_city_sim  # noqa: F401

print("\\n".join(sorted(n for n in sys.modules if n.startswith(("pygame", "pyxel")))))
"""
"""A whole interpreter whose only job is to import the simulation and report."""


def _display_modules_a_fresh_import_loads() -> list[str]:
    """Import the simulation in a new interpreter and list the display modules it pulled.

    ``sys.executable`` is this environment's interpreter, so the subprocess sees the same
    installed packages this test does and the reading is about this tree.
    """
    probe = subprocess.run(  # noqa: S603
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=False
    )
    assert probe.returncode == 0, f"the probe could not import the simulation:\n{probe.stderr}"
    return probe.stdout.split()


def test_importing_the_simulation_does_not_pull_in_a_display_or_a_socket() -> None:
    loaded = _display_modules_a_fresh_import_loads()
    assert not loaded, f"importing the simulation loaded {loaded}"


def test_the_reading_is_about_the_simulation_and_not_about_the_test_session() -> None:
    """The regression: pygame in *this* process must not change what the probe reports.

    pygame is imported here, inside the test, rather than at module scope. ``tests/sim``
    is a simulation test module and importing a display library to prove a point about
    the simulation at import time would be the mistake this file exists to catch; doing
    it inside one case keeps the rest of the module as pure as the package it measures.
    """
    pytest.importorskip("pygame")
    assert any(name.startswith("pygame") for name in sys.modules), (
        "this case has to contaminate the parent process for its claim to mean anything"
    )
    assert not _display_modules_a_fresh_import_loads()
