"""SQLSpec DuckDB integration coverage for MCP sync execution."""

from litestar.testing import TestClient

from tests.integration.apps import build_sqlspec_duckdb_app
from tests.integration.conftest import parse_tool_payload, rpc


def test_sqlspec_duckdb_tool_round_trip(duckdb_database_path: str) -> None:
    """The sync DuckDB suite should execute a real SQLSpec-backed MCP tool."""

    app = build_sqlspec_duckdb_app(duckdb_database_path)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]

        assert any(tool["name"] == "sqlspec_duckdb_create_report" for tool in tools)

        result = rpc(
            client,
            "tools/call",
            {"name": "sqlspec_duckdb_create_report", "arguments": {"title": "duckdb-report"}},
        )
        payload = parse_tool_payload(result)

        assert payload == {"title": "duckdb-report", "source": "duckdb"}
