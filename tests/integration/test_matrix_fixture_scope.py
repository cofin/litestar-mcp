"""Regression guards for the integration-matrix shared fixture scopes.

If one of these tests fails, the test matrix has lost its single-container
invariant (D4 in the test-matrix-auth-completion spec). Restore the
original session-scoped fixture before re-running the matrix.
"""

from typing import Any

import pytest


def _resolve_fixture_scope(request: pytest.FixtureRequest, fixture_name: str) -> str:
    """Return the pytest scope string for ``fixture_name`` as registered in ``request``."""
    manager: Any = request._fixturemanager
    name2fixturedefs = manager._arg2fixturedefs
    if fixture_name not in name2fixturedefs:
        msg = f"Fixture {fixture_name!r} is not registered in the active session."
        raise AssertionError(msg)
    fixture_defs = name2fixturedefs[fixture_name]
    # The final definition wins when multiple conftests contribute.
    return str(fixture_defs[-1].scope)


def test_postgres_service_is_session_scoped(request: pytest.FixtureRequest) -> None:
    """``postgres_service`` must stay session-scoped so only one container starts."""
    assert _resolve_fixture_scope(request, "postgres_service") == "session"
