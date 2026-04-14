"""Integration fixtures shared across the database-backed MCP test matrix."""

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
from litestar.testing import TestClient
from pytest_databases.docker.postgres import PostgresService

from tests.integration.apps import POSTGRES_TEST_TABLES


def _postgres_dsn(postgres_service: PostgresService) -> str:
    return (
        f"postgresql://{postgres_service.user}:{postgres_service.password}"
        f"@{postgres_service.host}:{postgres_service.port}/{postgres_service.database}"
    )


@pytest.fixture(autouse=True)
def reset_postgres_tables(request: pytest.FixtureRequest) -> None:
    """Drop the shared Postgres test tables before each integration test."""

    if "postgres_asyncpg_dsn" not in request.fixturenames and "postgres_sqlalchemy_dsn" not in request.fixturenames:
        return

    postgres_service = request.getfixturevalue("postgres_service")
    dsn = _postgres_dsn(postgres_service)
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table_name in POSTGRES_TEST_TABLES:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
        connection.commit()


@pytest.fixture
def postgres_asyncpg_dsn(postgres_service: PostgresService) -> str:
    """Postgres DSN for SQLSpec asyncpg-backed tests."""

    return _postgres_dsn(postgres_service)


@pytest.fixture
def postgres_sqlalchemy_dsn(postgres_service: PostgresService) -> str:
    """SQLAlchemy async Postgres connection string for Advanced Alchemy tests."""

    return _postgres_dsn(postgres_service).replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def duckdb_database_path(tmp_path: Path) -> str:
    """File-backed DuckDB path so sync sessions share state within a test."""

    return str(tmp_path / "integration-matrix.duckdb")


def rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: int = 1,
) -> dict[str, Any]:
    """Execute an MCP JSON-RPC request against the test app."""

    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post("/mcp", json=body)
    return response.json()


def parse_tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON payload returned in an MCP tool response."""

    return json.loads(result["result"]["content"][0]["text"])
