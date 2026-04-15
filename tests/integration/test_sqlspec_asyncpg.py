"""SQLSpec asyncpg integration coverage for MCP discovery and execution."""

import pytest
from litestar.testing import TestClient

from tests.integration.apps import build_sqlspec_asyncpg_app
from tests.integration.conftest import AUTH_MODES, auth_headers, parse_tool_payload, rpc


@pytest.mark.parametrize("auth_mode", AUTH_MODES)
def test_sqlspec_asyncpg_tool_round_trip(postgres_asyncpg_dsn: str, auth_mode: str) -> None:
    """SQLSpec asyncpg-backed handlers should execute real Postgres queries."""

    app = build_sqlspec_asyncpg_app(postgres_asyncpg_dsn, auth_mode=auth_mode)
    headers = auth_headers(auth_mode)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list", headers=headers)["result"]["tools"]

        assert any(tool["name"] == "sqlspec_create_report" for tool in tools)

        result = rpc(
            client,
            "tools/call",
            {"name": "sqlspec_create_report", "arguments": {"title": "asyncpg-report"}},
            headers=headers,
        )
        payload = parse_tool_payload(result)

        assert payload == {"title": "asyncpg-report", "source": "sqlspec"}
