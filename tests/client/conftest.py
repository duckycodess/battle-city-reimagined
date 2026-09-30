"""Headless setup for the client tests.

:mod:`client_helpers` sets the SDL driver variables when it is imported. SDL reads them
when a subsystem is initialised, not when pygame is imported, so what has to hold is that
nothing initialises a display before that module has been imported -- and
:func:`client_helpers.ensure_display` is the only thing in this package that initialises
one. pytest loads a conftest before the tests beside it, so importing ``client_helpers``
here puts the variables in place before any test can ask for a display.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from client_helpers import ensure_display

CLIENT_TESTS = Path(__file__).parent

_last_client_nodeid: str | None = None
"""The final collected test in this directory, decided once at collection time."""


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


def pytest_collection_finish(session: pytest.Session) -> None:
    """Record which selected test is the last one under ``tests/client``.

    Read at collection *finish*, not in ``pytest_collection_modifyitems``: a conftest's
    hook runs before the built-in one that applies ``-k`` and ``-m``, so it would see
    client tests that are about to be deselected and wait for a teardown that never
    comes.

    Deciding this from the collected list rather than from the next item in the queue
    makes the teardown below fire exactly once, at the right moment, whatever order the
    session ends up running in -- a ``-k`` filter, a reordering plugin, or an xdist
    worker that was handed an interleaved slice. Reading the neighbouring item instead
    would fire on every boundary, and a client test scheduled after one of those would
    find pygame gone from ``sys.modules`` and import a second copy of it.

    When a filter leaves no client test to run at all, collection has still imported the
    client's modules, and pygame with them. Nothing is going to need it, so it is
    released here instead -- before the first test of the session, rather than after a
    last client test that will never arrive.
    """
    global _last_client_nodeid
    ours = [item for item in session.items if _under_client_tests(item)]
    _last_client_nodeid = ours[-1].nodeid if ours else None
    if _last_client_nodeid is None:
        _release_pygame()


def pytest_runtest_teardown(item: pytest.Item) -> None:
    """After the last client test, put the interpreter back as the client found it.

    ``tests/sim/test_purity.py`` asserts that no ``pygame`` module is present in
    ``sys.modules``, as a proxy for "importing the simulation does not pull in a
    display". That proxy held while nothing in the repository used pygame. The client
    does, pytest runs the whole suite in one interpreter, and ``tests/client`` sorts
    before ``tests/sim``, so the client's import would be attributed to the simulation
    and would fail a check about a boundary the simulation never crossed.

    Shutting pygame down and dropping its modules here restores the precondition that
    check relies on. It weakens nothing: the substantive assertions in that file read the
    simulation's own source with :mod:`ast` and are untouched by anything in this
    process, and the simulation still imports nothing but the standard library.

    **This does not make the check correct, and it does not cover every way of running
    the suite.** pytest imports every selected test module during collection, so pygame
    is in ``sys.modules`` before the first test runs; this hook can only put it back
    afterwards. A session that runs the purity test *before* the last client test --
    ``pytest tests/sim tests/client``, for instance -- still fails it, and no code inside
    ``tests/client`` can prevent that. The claim is only really testable in a subprocess
    that imports ``battle_city_sim`` alone, which means editing ``tests/sim``; that is
    outside this issue's allowed files and is tracked as issue #22. The client package
    itself no longer contributes to the risk: importing ``battle_city_client`` does not
    import pygame, so the repository bootstrap contract in ``tests/test_bootstrap.py``
    stays clean on its own.
    """
    if item.nodeid != _last_client_nodeid:
        return
    _release_pygame()


def _release_pygame() -> None:
    """Shut pygame down and drop its modules from ``sys.modules``."""
    if pygame.get_init():
        pygame.quit()
    for name in [
        module for module in sys.modules if module == "pygame" or module.startswith("pygame.")
    ]:
        del sys.modules[name]


def _under_client_tests(item: pytest.Item) -> bool:
    return item.path is not None and item.path.is_relative_to(CLIENT_TESTS)
