"""Advanced Alchemy integration coverage for MCP discovery and execution."""

import json

from litestar.testing import TestClient

from tests.integration.apps import build_advanced_alchemy_app
from tests.integration.conftest import parse_tool_payload, rpc


def test_advanced_alchemy_tool_and_resource_round_trip(postgres_sqlalchemy_dsn: str) -> None:
    """Advanced Alchemy-backed tools and resources should execute against Postgres."""

    app = build_advanced_alchemy_app(postgres_sqlalchemy_dsn)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]
        resources = rpc(client, "resources/list")["result"]["resources"]

        assert any(tool["name"] == "aa_create_widget" for tool in tools)
        assert any(resource["name"] == "aa_widget_snapshot" for resource in resources)

        result = rpc(client, "tools/call", {"name": "aa_create_widget", "arguments": {"name": "alpha"}})
        payload = parse_tool_payload(result)

        assert payload["name"] == "alpha"

        resource = rpc(client, "resources/read", {"uri": "litestar://aa_widget_snapshot"})
        contents = json.loads(resource["result"]["contents"][0]["text"])

        assert any(item["name"] == "alpha" for item in contents)
