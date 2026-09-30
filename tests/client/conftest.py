"""Headless setup for the client tests.

Importing :mod:`client_helpers` is what sets the SDL driver variables, and it happens
here so that the variables are in place before any test module imports pygame. pytest
loads a conftest before the tests beside it, which makes this the only ordering the
suite has to rely on.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from client_helpers import ensure_display

CLIENT_TESTS = Path(__file__).parent


@pytest.fixture(autouse=True)
def display() -> Iterator[None]:
    """Guarantee an initialised dummy display for every test.

    Re-initialised per test rather than once per session because the smoke test drives
    :func:`battle_city_client.app.main`, which shuts pygame down on its way out.
    """
    ensure_display()
    yield


@pytest.fixture
def window() -> Iterator[pygame.Surface]:
    """A dummy window, for tests that exercise the presenter."""
    ensure_display()
    surface = pygame.display.set_mode((320, 240), pygame.RESIZABLE)
    yield surface


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """After the last client test, put the interpreter back as the client found it.

    ``tests/sim/test_purity.py`` asserts that no ``pygame`` module is present in
    ``sys.modules``, as a proxy for "importing the simulation does not pull in a
    display". That proxy held while no package in the repository used pygame. This one
    does, pytest runs the whole suite in one interpreter, and ``tests/client`` sorts
    before ``tests/sim``, so the client's import would be attributed to the simulation
    and would fail a check about a boundary the simulation never crossed.

    Shutting pygame down and dropping its modules here restores the precondition that
    check relies on. It weakens nothing: the substantive boundary assertions in that file
    read the simulation's own source with :mod:`ast` and are untouched by anything that
    happens in this process, and the simulation still imports nothing but the standard
    library.

    This is a workaround for a cross-package test, not a fix. The claim that file makes
    is only really testable in a subprocess that imports ``battle_city_sim`` alone, and
    rewriting it means editing ``tests/sim``, which this issue does not own. Flagged for
    a follow-up.
    """
    if nextitem is not None and _under_client_tests(nextitem):
        return
    if pygame.get_init():
        pygame.quit()
    for name in [
        module for module in sys.modules if module == "pygame" or module.startswith("pygame.")
    ]:
        del sys.modules[name]


def _under_client_tests(item: pytest.Item) -> bool:
    return item.path is not None and item.path.is_relative_to(CLIENT_TESTS)
