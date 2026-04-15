"""Integration tests for the MCP Streamable HTTP session lifecycle."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP

pytestmark = pytest.mark.integration


def _make_app() -> Litestar:
    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        return [{"id": 1, "name": "Alice"}]

    return Litestar(route_handlers=[list_users], plugins=[LitestarMCP()])


def _rpc(
    client: TestClient[Any],
    method: str,
    id_: int,
    params: "dict[str, Any] | None" = None,
    headers: "dict[str, str] | None" = None,
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


def test_end_to_end_post_only_session() -> None:
    app = _make_app()
    with TestClient(app=app) as client:
        # 1. initialize
        init = _rpc(
            client,
            "initialize",
            1,
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "it"}},
        )
        assert init.status_code == 200
        session_id = init.headers["mcp-session-id"]
        assert session_id

        # 2. notifications/initialized
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": session_id},
        )

        # 3. tools/list
        listed = _rpc(client, "tools/list", 2, headers={"Mcp-Session-Id": session_id})
        assert listed.status_code == 200
        assert listed.headers["mcp-session-id"] == session_id
        tool_names = {t["name"] for t in listed.json()["result"]["tools"]}
        assert "list_users" in tool_names

        # 4. tools/call
        called = _rpc(
            client,
            "tools/call",
            3,
            {"name": "list_users", "arguments": {}},
            headers={"Mcp-Session-Id": session_id},
        )
        assert called.status_code == 200
        assert called.headers["mcp-session-id"] == session_id
        assert "result" in called.json()

        # 5. DELETE
        deleted = client.delete("/mcp", headers={"Mcp-Session-Id": session_id})
        assert deleted.status_code == 204

        # 6. Post-delete call fails 404
        after = _rpc(client, "tools/list", 4, headers={"Mcp-Session-Id": session_id})
        assert after.status_code == 404
