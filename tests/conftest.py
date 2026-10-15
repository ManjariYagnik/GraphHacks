"""Make real-database checks explicit and safe to run on a developer machine."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-integration", action="store_true", help="Test against real FalkorDB")
    parser.addoption(
        "--run-persistence", action="store_true", help="Also restart the local Compose database"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "persistence" in item.keywords and not config.getoption("--run-persistence"):
            item.add_marker(
                pytest.mark.skip(reason="requires --run-persistence (restarts database)")
            )
        elif "integration" in item.keywords and not (
            config.getoption("--run-integration") or config.getoption("--run-persistence")
        ):
            item.add_marker(pytest.mark.skip(reason="requires --run-integration"))
