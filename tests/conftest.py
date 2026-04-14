"""Global pytest configuration shared across unit and integration suites."""

import pytest

pytest_plugins = ["pytest_databases.docker.postgres"]


def pytest_configure(config: pytest.Config) -> None:
    """Register markers for selective unit and integration test runs."""

    config.addinivalue_line("markers", "unit: marks tests that do not require external services")
    config.addinivalue_line("markers", "integration: marks tests that require real service backends")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply default markers based on the split test tree."""

    for item in items:
        path_parts = set(item.path.parts)
        if "unit" in path_parts:
            item.add_marker(pytest.mark.unit)
        if "integration" in path_parts:
            item.add_marker(pytest.mark.integration)
