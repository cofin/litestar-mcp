"""Advanced Alchemy + Dishka integration coverage for MCP execution."""

import pytest
from litestar.testing import TestClient

from tests.integration.apps import AuthMode, build_advanced_alchemy_dishka_app
from tests.integration.conftest import AUTH_MODES, auth_headers, parse_tool_payload, rpc


@pytest.mark.parametrize("auth_mode", AUTH_MODES)
def test_advanced_alchemy_dishka_tool_round_trip(postgres_sqlalchemy_dsn: str, auth_mode: AuthMode) -> None:
    """Dishka-backed Advanced Alchemy handlers should execute through MCP."""

    app = build_advanced_alchemy_dishka_app(postgres_sqlalchemy_dsn, auth_mode=auth_mode)
    headers = auth_headers(auth_mode)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list", headers=headers)["result"]["tools"]

        assert any(tool["name"] == "aa_dishka_create_widget" for tool in tools)

        result = rpc(
            client,
            "tools/call",
            {"name": "aa_dishka_create_widget", "arguments": {"name": "beta"}},
            headers=headers,
        )
        payload = parse_tool_payload(result)

        assert payload == {"id": payload["id"], "name": "beta", "source": "dishka"}
