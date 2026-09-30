"""The simulation boundary: no I/O, no clock, no shared randomness.

The architecture specification forbids the simulation from reading clocks, frames,
sockets, files, the environment or unseeded randomness. A review can miss a new import,
so the boundary is asserted mechanically over the package source.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import battle_city_sim

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


def test_importing_the_simulation_does_not_pull_in_a_display_or_a_socket() -> None:
    loaded = {name for name in sys.modules if name.startswith(("pygame", "pyxel"))}
    assert not loaded, f"importing the simulation loaded {sorted(loaded)}"
