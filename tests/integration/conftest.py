"""Integration fixtures shared across the database-backed MCP test matrix."""

import json
from typing import TYPE_CHECKING, Any, cast

import psycopg
import pytest
from litestar.testing import AsyncTestClient, TestClient

from tests.integration.apps import POSTGRES_TEST_TABLES, AuthMode

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_databases.docker.postgres import PostgresService


def _postgres_dsn(postgres_service: "PostgresService") -> "str":
    return (
        f"postgresql://{postgres_service.user}:{postgres_service.password}"
        f"@{postgres_service.host}:{postgres_service.port}/{postgres_service.database}"
    )


@pytest.fixture(scope="session", autouse=True)
def _ensure_postgres_tables(postgres_service: "PostgresService") -> "None":
    """Ensure the Postgres service is ready. Tables are created by the apps."""
    # This fixture now just acts as a session-level dependency on the service
    _ = _postgres_dsn(postgres_service)


@pytest.fixture(autouse=True)
def reset_postgres_tables(request: "pytest.FixtureRequest") -> "None":
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
def postgres_asyncpg_dsn(postgres_service: "PostgresService") -> "str":
    """Postgres DSN for SQLSpec asyncpg-backed tests."""

    return _postgres_dsn(postgres_service)


@pytest.fixture(scope="session")
def postgres_sqlalchemy_dsn(postgres_service: "PostgresService") -> "str":
    """SQLAlchemy async Postgres connection string for Advanced Alchemy tests."""

    return _postgres_dsn(postgres_service).replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def duckdb_database_path(tmp_path: "Path") -> "str":
    """File-backed DuckDB path so sync sessions share state within a test."""

    return str(tmp_path / "integration-matrix.duckdb")


AUTH_MODES: "tuple[AuthMode, ...]" = ("none", "bearer")


@pytest.fixture(autouse=True)
def modern_direct_mcp_requests(monkeypatch: "pytest.MonkeyPatch") -> "None":
    """Add the modern envelope to integration tests that issue MCP directly."""
    sync_post = TestClient.post
    async_post = AsyncTestClient.post

    def enrich(url: str, kwargs: dict[str, Any]) -> None:
        if not url.rstrip("/").endswith("mcp"):
            return
        payload = kwargs.get("json")
        if not isinstance(payload, dict) or not isinstance(payload.get("method"), str):
            return
        method = payload["method"]
        params = payload.setdefault("params", {})
        if not isinstance(params, dict):
            return
        meta = params.setdefault("_meta", {})
        if isinstance(meta, dict):
            meta["io.modelcontextprotocol/protocolVersion"] = "2026-07-28"
            meta.setdefault("io.modelcontextprotocol/clientCapabilities", {})
            meta.setdefault(
                "io.modelcontextprotocol/clientInfo",
                {"name": "integration-tests", "version": "1"},
            )
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("MCP-Protocol-Version", "2026-07-28")
        headers.setdefault("Mcp-Method", method)
        name_field = {
            "tools/call": "name",
            "resources/read": "uri",
            "prompts/get": "name",
            "tasks/get": "taskId",
            "tasks/update": "taskId",
            "tasks/cancel": "taskId",
        }.get(method)
        if name_field is not None:
            headers.setdefault("Mcp-Name", str(params.get(name_field, "")))
        kwargs["headers"] = headers

    def patched_sync(client: TestClient[Any], url: str, *args: Any, **kwargs: Any) -> Any:
        enrich(url, kwargs)
        return sync_post(client, url, *args, **kwargs)

    async def patched_async(client: AsyncTestClient[Any], url: str, *args: Any, **kwargs: Any) -> Any:
        enrich(url, kwargs)
        return await async_post(client, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "post", patched_sync)
    monkeypatch.setattr(AsyncTestClient, "post", patched_async)


def auth_headers(auth_mode: "AuthMode") -> "dict[str, str]":
    """Return the HTTP ``Authorization`` header(s) for the given auth mode.

    In ``"none"`` mode this returns an empty dict; in ``"bearer"`` mode it
    returns a ``{"Authorization": "Bearer <token>"}`` dict using the
    pre-minted ``VALID_TOKEN`` from ``tests/integration/_auth.py``.
    """
    if auth_mode == "bearer":
        from tests.integration._auth import VALID_TOKEN

        return {"Authorization": f"Bearer {VALID_TOKEN}"}
    return {}


def _modern_headers(
    method: "str",
    params: "dict[str, Any]",
    headers: "dict[str, str] | None",
) -> "dict[str, str]":
    final_headers = dict(headers or {})
    final_headers.setdefault("Accept", "application/json, text/event-stream")
    final_headers.setdefault("MCP-Protocol-Version", "2026-07-28")
    final_headers.setdefault("Mcp-Method", method)
    name_fields = {
        "tools/call": "name",
        "resources/read": "uri",
        "prompts/get": "name",
        "tasks/get": "taskId",
        "tasks/update": "taskId",
        "tasks/cancel": "taskId",
    }
    if method in name_fields:
        final_headers.setdefault("Mcp-Name", str(params.get(name_fields[method], "")))
    return final_headers


def _modern_params(params: "dict[str, Any] | None") -> "dict[str, Any]":
    modern = dict(params or {})
    modern["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "integration-tests", "version": "1"},
    }
    return modern


def rpc(
    client: "TestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: "int" = 1,
    headers: "dict[str, str] | None" = None,
) -> "dict[str, Any]":
    """Execute an MCP JSON-RPC request against the test app."""

    request_params = _modern_params(params)
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": request_params}
    response = client.post("/mcp", json=body, headers=_modern_headers(method, request_params, headers))
    return cast("dict[str, Any]", response.json())


def rpc_response(
    client: "TestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: "int" = 1,
    headers: "dict[str, str] | None" = None,
) -> "Any":
    """Execute an MCP JSON-RPC request and return the raw HTTP response."""

    request_params = _modern_params(params)
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": request_params}
    return client.post("/mcp", json=body, headers=_modern_headers(method, request_params, headers))


def parse_tool_payload(result: "dict[str, Any]") -> "dict[str, Any]":
    """Decode the JSON payload returned in an MCP tool response."""

    return cast("dict[str, Any]", json.loads(result["result"]["content"][0]["text"]))
