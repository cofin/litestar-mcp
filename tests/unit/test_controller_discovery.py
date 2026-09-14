"""Tests for Controller-based MCP route discovery."""

from typing import Any

from litestar import Controller, Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.utils import mcp_tool
from tests.unit.conftest import mcp_post


def _rpc(client: "TestClient[Any]", method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
    data: dict[str, Any] = mcp_post(client, method, params).json()
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
