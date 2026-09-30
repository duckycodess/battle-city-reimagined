"""The protocol boundary: no project dependencies, no sockets, no event loop.

The architecture specification keeps content and protocol independent of client and
server, and the protocol package is further meant to be usable by a tool that never
loads a game. A review can miss a new import, so the boundary is asserted mechanically
over the package source.

The absence of ``asyncio`` is the interesting one. Defining an awaitable interface does
not require an event loop library, so the transport seam is neutral about the framework
as well as about the socket: a peer built on anything that can await is free to
implement it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import battle_city_protocol

ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "json",
        "math",
        "re",
        "typing",
    }
)
"""Standard library modules the protocol may use. ``json`` is the wire format."""

FORBIDDEN_NAMES = (
    "asyncio",
    "socket",
    "ssl",
    "selectors",
    "os",
    "sys",
    "pathlib",
    "time",
    "datetime",
    "random",
    "pickle",
    "marshal",
    "shelve",
    "subprocess",
    "threading",
    "pygame",
)
"""Transport, process, clock and — pointedly — every object deserialiser."""

PACKAGE_ROOT = Path(battle_city_protocol.__file__).resolve().parent


def _modules() -> tuple[Path, ...]:
    found = tuple(sorted(PACKAGE_ROOT.glob("*.py")))
    assert found, "the protocol package should contain modules"
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
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
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


def test_the_package_never_evaluates_what_a_peer_sent() -> None:
    """No ``eval``, no ``exec``, no import by name: a frame is data, never a program."""
    banned = {"eval", "exec", "compile", "__import__", "getattr", "setattr"}
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned, f"{module.name} calls {node.func.id}"
