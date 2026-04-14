"""SQLSpec asyncpg integration coverage for MCP discovery and execution."""

from litestar.testing import TestClient

from tests.integration.apps import build_sqlspec_asyncpg_app
from tests.integration.conftest import parse_tool_payload, rpc


def test_sqlspec_asyncpg_tool_round_trip(postgres_asyncpg_dsn: str) -> None:
    """SQLSpec asyncpg-backed handlers should execute real Postgres queries."""

    app = build_sqlspec_asyncpg_app(postgres_asyncpg_dsn)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]

        assert any(tool["name"] == "sqlspec_create_report" for tool in tools)

        result = rpc(
            client,
            "tools/call",
            {"name": "sqlspec_create_report", "arguments": {"title": "asyncpg-report"}},
        )
        payload = parse_tool_payload(result)

        assert payload == {"title": "asyncpg-report", "source": "sqlspec"}
