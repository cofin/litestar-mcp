"""SQLSpec DuckDB integration coverage for MCP sync execution."""

import pytest
from litestar.testing import TestClient

from tests.integration.apps import AuthMode, build_sqlspec_duckdb_app
from tests.integration.conftest import AUTH_MODES, auth_headers, parse_tool_payload, rpc


@pytest.mark.parametrize("auth_mode", AUTH_MODES)
def test_sqlspec_duckdb_tool_round_trip(duckdb_database_path: str, auth_mode: AuthMode) -> None:
    """The sync DuckDB suite should execute a real SQLSpec-backed MCP tool."""

    app = build_sqlspec_duckdb_app(duckdb_database_path, auth_mode=auth_mode)
    headers = auth_headers(auth_mode)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list", headers=headers)["result"]["tools"]

        assert any(tool["name"] == "sqlspec_duckdb_create_report" for tool in tools)

        result = rpc(
            client,
            "tools/call",
            {"name": "sqlspec_duckdb_create_report", "arguments": {"title": "duckdb-report"}},
            headers=headers,
        )
        payload = parse_tool_payload(result)

        assert payload == {"title": "duckdb-report", "source": "duckdb"}
