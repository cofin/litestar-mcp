"""Integration fixtures shared across the database-backed MCP test matrix."""

import json
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from litestar.testing import TestClient
from pytest_databases.docker.postgres import PostgresService

from tests.integration.apps import POSTGRES_TEST_TABLES, AuthMode


def _postgres_dsn(postgres_service: PostgresService) -> str:
    return (
        f"postgresql://{postgres_service.user}:{postgres_service.password}"
        f"@{postgres_service.host}:{postgres_service.port}/{postgres_service.database}"
    )


@pytest.fixture(scope="session", autouse=True)
def _ensure_postgres_tables(postgres_service: PostgresService) -> None:
    """Ensure the Postgres service is ready. Tables are created by the apps."""
    # This fixture now just acts as a session-level dependency on the service
    _ = _postgres_dsn(postgres_service)


@pytest.fixture(autouse=True)
def reset_postgres_tables(request: pytest.FixtureRequest) -> None:
    """Delete all data from shared Postgres test tables before each integration test."""

    if "postgres_asyncpg_dsn" not in request.fixturenames and "postgres_sqlalchemy_dsn" not in request.fixturenames:
        return

    from psycopg import sql

    postgres_service = request.getfixturevalue("postgres_service")
    dsn = _postgres_dsn(postgres_service)
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        # Terminate other sessions to this database to prevent locking
        cursor.execute(
            """
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = current_database()
                  AND pid <> pg_backend_pid();
                """
        )
        for table_name in POSTGRES_TEST_TABLES:
            # Use DELETE FROM instead of DROP or TRUNCATE as requested
            try:
                cursor.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table_name)))
            except psycopg.errors.UndefinedTable:
                # Table might not be created yet by a specific app factory
                continue


@pytest.fixture(scope="session")
def postgres_asyncpg_dsn(postgres_service: PostgresService) -> str:
    """Postgres DSN for SQLSpec asyncpg-backed tests."""

    return _postgres_dsn(postgres_service)


@pytest.fixture(scope="session")
def postgres_sqlalchemy_dsn(postgres_service: PostgresService) -> str:
    """SQLAlchemy async Postgres connection string for Advanced Alchemy tests."""

    return _postgres_dsn(postgres_service).replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def duckdb_database_path(tmp_path: Path) -> str:
    """File-backed DuckDB path so sync sessions share state within a test."""

    return str(tmp_path / "integration-matrix.duckdb")


AUTH_MODES: tuple[AuthMode, ...] = ("none", "bearer")


def auth_headers(auth_mode: AuthMode) -> dict[str, str]:
    """Return the HTTP ``Authorization`` header(s) for the given auth mode.

    In ``"none"`` mode this returns an empty dict; in ``"bearer"`` mode it
    returns a ``{"Authorization": "Bearer <token>"}`` dict using the
    pre-minted ``VALID_TOKEN`` from ``tests/integration/_auth.py``.
    """
    if auth_mode == "bearer":
        from tests.integration._auth import VALID_TOKEN

        return {"Authorization": f"Bearer {VALID_TOKEN}"}
    return {}


def _ensure_session(client: TestClient[Any], headers: "dict[str, str] | None" = None) -> str:
    """Lazily initialize an MCP session per (client, auth) pair and cache it."""
    auth_token = (headers or {}).get("Authorization", "") or (headers or {}).get("authorization", "")
    key = f"_mcp_session::{auth_token}"
    sid = getattr(client, key, None)
    if sid:
        return cast("str", sid)
    init_headers = dict(headers or {})
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "it"}},
        },
        headers=init_headers,
    )
    sid_val = init.headers.get("mcp-session-id", "")
    if sid_val:
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={**init_headers, "Mcp-Session-Id": sid_val},
        )
    setattr(client, key, sid_val)
    return str(sid_val)


def _inject_session_header(
    client: TestClient[Any],
    method: str,
    headers: "dict[str, str] | None",
) -> dict[str, str]:
    final_headers = dict(headers or {})
    if method == "initialize":
        return final_headers
    if "Mcp-Session-Id" in final_headers or "mcp-session-id" in final_headers:
        return final_headers
    sid = _ensure_session(client, headers)
    if sid:
        final_headers["Mcp-Session-Id"] = sid
    return final_headers


def rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: int = 1,
    headers: "dict[str, str] | None" = None,
) -> dict[str, Any]:
    """Execute an MCP JSON-RPC request against the test app."""

    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post("/mcp", json=body, headers=_inject_session_header(client, method, headers))
    return cast("dict[str, Any]", response.json())


def rpc_response(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: int = 1,
    headers: "dict[str, str] | None" = None,
) -> Any:
    """Execute an MCP JSON-RPC request and return the raw HTTP response."""

    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=_inject_session_header(client, method, headers))


def parse_tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON payload returned in an MCP tool response."""

    return cast("dict[str, Any]", json.loads(result["result"]["content"][0]["text"]))
