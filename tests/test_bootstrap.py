"""Bootstrap contract: each planned workspace package is importable."""

import importlib


def test_workspace_packages_import() -> None:
    for module in (
        "battle_city_sim",
        "battle_city_content",
        "battle_city_protocol",
        "battle_city_client",
        "battle_city_server",
        "battle_city_ai",
        "battle_city_tools",
    ):
        importlib.import_module(module)
