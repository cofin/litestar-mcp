"""SQLSpec + Dishka integration coverage for MCP discovery and execution."""

from litestar.testing import TestClient

from tests.integration.apps import build_sqlspec_dishka_app
from tests.integration.conftest import parse_tool_payload, rpc


def test_sqlspec_dishka_tool_round_trip(postgres_asyncpg_dsn: str) -> None:
    """Dishka-backed SQLSpec handlers should execute through MCP."""

    app = build_sqlspec_dishka_app(postgres_asyncpg_dsn)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]

        assert any(tool["name"] == "sqlspec_dishka_create_report" for tool in tools)

        result = rpc(
            client,
            "tools/call",
            {"name": "sqlspec_dishka_create_report", "arguments": {"title": "dishka-report"}},
        )
        payload = parse_tool_payload(result)

        assert payload == {"title": "dishka-report", "source": "sqlspec-dishka"}
