"""Auth failure matrix across every persistence backend (Phase 4).

For each `(backend, failure_mode)` cell we build the app in
``auth_mode="bearer"`` and assert:

* The JSON-RPC ``tools/list`` call is rejected with HTTP 401.
* The response body does not leak tool names.
* The ``.well-known/oauth-protected-resource`` endpoint still returns the
  configured issuer, regardless of auth state.

Backends x failure modes = 5 x 3 = 15 parametrized cases. A sixth
"happy-path" sanity cell per backend is intentionally out of scope here —
the `test_<backend>.py` modules already cover that with a valid token.
"""

from collections.abc import Callable
from typing import Any

import pytest
from litestar.testing import TestClient

from tests.integration._auth import (
    AUDIENCE,
    EXPIRED_TOKEN,
    FORGED_TOKEN,
    ISSUER,
    VALID_TOKEN,
)
from tests.integration.apps import (
    build_advanced_alchemy_app,
    build_advanced_alchemy_dishka_app,
    build_sqlspec_asyncpg_app,
    build_sqlspec_dishka_app,
    build_sqlspec_duckdb_app,
)
from tests.integration.conftest import rpc_response

BACKENDS: list[tuple[str, Callable[..., Any], str]] = [
    ("advanced_alchemy", build_advanced_alchemy_app, "postgres_sqlalchemy_dsn"),
    ("advanced_alchemy_dishka", build_advanced_alchemy_dishka_app, "postgres_sqlalchemy_dsn"),
    ("sqlspec_asyncpg", build_sqlspec_asyncpg_app, "postgres_asyncpg_dsn"),
    ("sqlspec_dishka", build_sqlspec_dishka_app, "postgres_asyncpg_dsn"),
    ("sqlspec_duckdb", build_sqlspec_duckdb_app, "duckdb_database_path"),
]

FAILURE_MODES = ("missing_token", "forged_token", "expired_token")

# Tool names that must not leak in 401 bodies. This covers every
# `mcp_tool` name emitted by the factories above.
KNOWN_TOOL_NAMES = (
    "aa_create_widget",
    "aa_dishka_create_widget",
    "sqlspec_create_report",
    "sqlspec_dishka_create_report",
    "sqlspec_duckdb_create_report",
)


def _headers_for_failure(mode: str) -> "dict[str, str]":
    if mode == "missing_token":
        return {}
    if mode == "forged_token":
        return {"Authorization": f"Bearer {FORGED_TOKEN}"}
    if mode == "expired_token":
        return {"Authorization": f"Bearer {EXPIRED_TOKEN}"}
    msg = f"unknown failure mode: {mode}"  # pragma: no cover - guard
    raise AssertionError(msg)


@pytest.mark.parametrize(
    ("backend_name", "factory", "dsn_fixture"),
    BACKENDS,
    ids=[name for name, _, _ in BACKENDS],
)
@pytest.mark.parametrize("failure_mode", FAILURE_MODES)
def test_auth_failure_rejects_request(
    request: pytest.FixtureRequest,
    backend_name: str,
    factory: Callable[..., Any],
    dsn_fixture: str,
    failure_mode: str,
) -> None:
    """Every backend x failure mode must produce a 401 with no tool leakage."""
    dsn = request.getfixturevalue(dsn_fixture)
    app = factory(dsn, auth_mode="bearer")
    headers = _headers_for_failure(failure_mode)

    with TestClient(app=app) as client:
        response = rpc_response(client, "tools/list", headers=headers)

        assert response.status_code == 401, (
            f"backend={backend_name} mode={failure_mode} expected 401, got {response.status_code}"
        )
        body_text = response.text
        for tool_name in KNOWN_TOOL_NAMES:
            assert tool_name not in body_text, (
                f"backend={backend_name} mode={failure_mode} leaked tool {tool_name!r} in 401 body"
            )


@pytest.mark.parametrize(
    ("backend_name", "factory", "dsn_fixture"),
    BACKENDS,
    ids=[name for name, _, _ in BACKENDS],
)
def test_well_known_protected_resource_available_without_auth(
    request: pytest.FixtureRequest,
    backend_name: str,
    factory: Callable[..., Any],
    dsn_fixture: str,
) -> None:
    """``.well-known/oauth-protected-resource`` must always expose the issuer."""
    dsn = request.getfixturevalue(dsn_fixture)
    app = factory(dsn, auth_mode="bearer")

    with TestClient(app=app) as client:
        # No Authorization header — the well-known route is exempt.
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200, f"backend={backend_name}"
        data = resp.json()
        assert ISSUER in data["authorization_servers"]
        assert data["resource"] == AUDIENCE


@pytest.mark.parametrize(
    ("backend_name", "factory", "dsn_fixture"),
    BACKENDS,
    ids=[name for name, _, _ in BACKENDS],
)
def test_valid_token_is_accepted(
    request: pytest.FixtureRequest,
    backend_name: str,
    factory: Callable[..., Any],
    dsn_fixture: str,
) -> None:
    """A valid bearer token must be accepted by every bearer-mode app."""
    dsn = request.getfixturevalue(dsn_fixture)
    app = factory(dsn, auth_mode="bearer")
    headers = {"Authorization": f"Bearer {VALID_TOKEN}"}

    with TestClient(app=app) as client:
        response = rpc_response(client, "tools/list", headers=headers)
        assert response.status_code == 200, f"backend={backend_name}"
        assert "result" in response.json()
