"""The AI package's dependency boundary, asserted over its source rather than reviewed.

The architecture specification fixes the runtime dependency direction: AI depends on the
simulation and on nothing else in the project. The determinism promise adds a second
boundary - no clock, no frame counter, no socket, no file, no environment, no shared
global randomness - and a single new import is enough to break it silently. The
simulation asserts the same thing about itself in ``tests/sim/test_purity.py``; this is
the AI side of that boundary.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import battle_city_ai

ALLOWED_STANDARD_LIBRARY = frozenset(
    {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "typing",
    }
)
"""Standard library modules the AI package may use: type and data plumbing only."""

ALLOWED_PROJECT_PACKAGES = frozenset({"battle_city_sim"})
"""Project packages the AI package may import. Content, protocol, client, server and
tools are all forbidden: a bot that reached for them would create the cycle the
architecture specification's dependency order exists to prevent."""

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

PACKAGE_ROOT = Path(battle_city_ai.__file__).resolve().parent


def _modules() -> tuple[Path, ...]:
    found = tuple(sorted(PACKAGE_ROOT.glob("*.py")))
    assert found, "the AI package should contain modules"
    return found


def _absolute_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_package_imports_only_allowed_modules() -> None:
    allowed = ALLOWED_STANDARD_LIBRARY | ALLOWED_PROJECT_PACKAGES
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        offenders = _absolute_imports(tree) - allowed
        assert not offenders, f"{module.name} imports {sorted(offenders)}"


def test_the_package_never_imports_a_forbidden_module() -> None:
    for module in _modules():
        imported = _absolute_imports(
            ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        )
        for name in FORBIDDEN_NAMES:
            assert name not in imported, f"{module.name} imports {name}"


def test_the_package_depends_on_no_project_package_but_the_simulation() -> None:
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for name in _absolute_imports(tree):
            if not name.startswith("battle_city_"):
                continue
            assert name in ALLOWED_PROJECT_PACKAGES, f"{module.name} imports {name}"


def test_importing_the_package_does_not_pull_in_a_display_or_a_socket() -> None:
    """Import the package alone in a clean interpreter and report what came with it.

    Asserted in a subprocess rather than against this process's ``sys.modules``, because
    this process is not evidence of anything. pytest imports every selected test module
    during collection, so a sibling suite that uses pygame puts it in ``sys.modules``
    before the first test runs; ``tests/sim/test_purity.py`` makes the in-process claim
    and ``tests/client/conftest.py`` spends a long docstring on the fact that it can only
    paper over it for suites that sort after ``tests/client``. ``tests/ai`` sorts before
    it, so the in-process form of this check asserted the collection order of the whole
    repository and failed on a full run for a reason that had nothing to do with the AI
    package. A fresh interpreter answers the question that was actually being asked.
    """
    script = (
        "import sys;"
        "import battle_city_ai;"
        "print(sorted(n for n in sys.modules if n.startswith(('pygame', 'pyxel'))))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert loaded == "[]", f"importing the AI package loaded {loaded}"
