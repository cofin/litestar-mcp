"""Tests for Controller-based MCP route discovery."""

from typing import Any

from litestar import Controller, Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.utils import mcp_tool

PROTOCOL_VERSION = "2026-07-28"
_NAME_FIELDS = {"tools/call": "name", "resources/read": "uri", "prompts/get": "name"}


def _rpc(client: "TestClient[Any]", method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {"MCP-Protocol-Version": PROTOCOL_VERSION, "Mcp-Method": method}
    name_field = _NAME_FIELDS.get(method)
    if name_field is not None:
        headers["Mcp-Name"] = str(request_params.get(name_field, ""))
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": request_params}
    data: dict[str, Any] = client.post("/mcp", json=body, headers=headers).json()
    return data


def test_controller_discovery() -> "None":
    class MyController(Controller):
        path = "/test"

        @mcp_tool(name="controller_tool")
        @get("/tool", sync_to_thread=False)
        def my_tool(self) -> "str":
            return "hello"

    plugin = LitestarMCP()
    app = Litestar(route_handlers=[MyController], plugins=[plugin])

    with TestClient(app=app) as client:
        result = _rpc(client, "tools/list")
        tools = result["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "controller_tool" in tool_names
