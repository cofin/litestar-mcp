"""Tests for Controller-based MCP route discovery."""

from typing import Any

from litestar import Controller, Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.decorators import mcp_tool


def _ensure_session(client: TestClient[Any]) -> str:
    sid = getattr(client, "_mcp_session", None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    client._mcp_session = sid  # type: ignore[attr-defined]
    return str(sid)


def _rpc(client: TestClient[Any], method: str, params: "dict[str, Any] | None" = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def test_controller_discovery() -> None:
    class MyController(Controller):
        path = "/test"

        @mcp_tool(name="controller_tool")
        @get("/tool")
        def my_tool(self) -> str:
            return "hello"

    plugin = LitestarMCP()
    app = Litestar(route_handlers=[MyController], plugins=[plugin])

    with TestClient(app=app) as client:
        result = _rpc(client, "tools/list")
        tools = result["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "controller_tool" in tool_names
