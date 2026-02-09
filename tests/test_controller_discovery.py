"""Tests for Controller-based MCP route discovery."""

from litestar import Controller, Litestar, get
from litestar.testing import TestClient
from litestar_mcp import LitestarMCP
from litestar_mcp.decorators import mcp_tool

def test_controller_discovery() -> None:
    class MyController(Controller):
        path = "/test"
        
        @mcp_tool(name="controller_tool")
        @get("/tool")
        def my_tool(self) -> str:
            return "hello"

    plugin = LitestarMCP()
    app = Litestar(route_handlers=[MyController], plugins=[plugin])
    
    # Discovery for controllers should happen during startup
    # TestClient triggers startup hooks
    with TestClient(app=app) as client:
        response = client.get("/mcp/tools")
        assert response.status_code == 200
        tools = response.json()["tools"]
        tool_names = [t["name"] for t in tools]
        assert "controller_tool" in tool_names
