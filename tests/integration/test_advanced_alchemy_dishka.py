"""Advanced Alchemy + Dishka integration coverage for MCP execution."""

from litestar.testing import TestClient

from tests.integration.apps import build_advanced_alchemy_dishka_app
from tests.integration.conftest import parse_tool_payload, rpc


def test_advanced_alchemy_dishka_tool_round_trip(postgres_sqlalchemy_dsn: str) -> None:
    """Dishka-backed Advanced Alchemy handlers should execute through MCP."""

    app = build_advanced_alchemy_dishka_app(postgres_sqlalchemy_dsn)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]

        assert any(tool["name"] == "aa_dishka_create_widget" for tool in tools)

        result = rpc(client, "tools/call", {"name": "aa_dishka_create_widget", "arguments": {"name": "beta"}})
        payload = parse_tool_payload(result)

        assert payload == {"id": payload["id"], "name": "beta", "source": "dishka"}
