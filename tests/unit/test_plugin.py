"""Tests for LitestarMCP integration."""

from __future__ import annotations

import json
from typing import Any

from litestar import Litestar, get, post
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.config import MCPConfig


def _ensure_session(client: TestClient[Any], base: str = "/mcp") -> str:
    key = f"_mcp_session::{base}"
    sid = getattr(client, key, None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
    init = client.post(
        base,
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        base,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    setattr(client, key, sid)
    return str(sid)


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    msg_id: int = 1,
    base: str = "/mcp",
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client, base)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post(base, json=body, headers=headers).json()  # type: ignore[no-any-return]


class TestLitestarMCP:
    """Test suite for LitestarMCP."""

    def test_plugin_initialization_default(self) -> None:
        plugin = LitestarMCP()
        assert plugin.config.base_path == "/mcp"

    def test_plugin_initialization_custom(self) -> None:
        config = MCPConfig(base_path="/api/mcp")
        plugin = LitestarMCP(config)
        assert plugin.config.base_path == "/api/mcp"

    def test_plugin_discovers_mcp_routes(self) -> None:
        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        @get("/config", opt={"mcp_resource": "app_config"})
        async def get_config() -> dict[str, Any]:
            return {"debug": True}

        @get("/regular")
        async def regular_route() -> dict[str, Any]:
            return {"message": "regular"}

        plugin = LitestarMCP()
        Litestar(plugins=[plugin], route_handlers=[get_users, get_config, regular_route])

        assert "list_users" in plugin.discovered_tools
        assert "app_config" in plugin.discovered_resources
        assert len(plugin.discovered_tools) == 1
        assert len(plugin.discovered_resources) == 1

    def test_mcp_endpoints_work(self) -> None:
        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[get_users])
        client = TestClient(app=app)

        # Initialize
        result = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        assert "serverInfo" in result["result"]
        assert "capabilities" in result["result"]

        # tools/list
        result = _rpc(client, "tools/list")
        assert len(result["result"]["tools"]) == 1
        assert result["result"]["tools"][0]["name"] == "list_users"

        # resources/list (openapi is always present)
        result = _rpc(client, "resources/list")
        assert len(result["result"]["resources"]) == 1
        assert result["result"]["resources"][0]["name"] == "openapi"

    def test_openapi_resource_access(self) -> None:
        app = Litestar(plugins=[LitestarMCP()])
        client = TestClient(app=app)

        result = _rpc(client, "resources/read", {"uri": "litestar://openapi"})
        contents = result["result"]["contents"]
        assert contents[0]["uri"] == "litestar://openapi"

    def test_tool_execution_real(self) -> None:
        @post("/analyze", opt={"mcp_tool": "analyze_data"})
        async def analyze(data: dict[str, Any]) -> dict[str, Any]:
            return {"result": "analyzed", "input": data}

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[analyze])
        client = TestClient(app=app)

        test_data = {"test": "data", "count": 42}
        result = _rpc(client, "tools/call", {"name": "analyze_data", "arguments": {"data": test_data}})
        content = result["result"]["content"]
        parsed = json.loads(content[0]["text"])
        assert parsed["result"] == "analyzed"
        assert parsed["input"] == test_data

    def test_error_handling(self) -> None:
        app = Litestar(plugins=[LitestarMCP()])
        client = TestClient(app=app)

        result = _rpc(client, "resources/read", {"uri": "litestar://nonexistent"})
        assert "error" in result

        result = _rpc(client, "tools/call", {"name": "nonexistent", "arguments": {}})
        assert "error" in result

    def test_openapi_integration(self) -> None:
        from litestar.openapi.config import OpenAPIConfig

        app = Litestar(plugins=[LitestarMCP()], openapi_config=OpenAPIConfig(title="My Custom API", version="2.1.0"))
        client = TestClient(app=app)

        result = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        server_info = result["result"]["serverInfo"]
        assert server_info["name"] == "My Custom API"
        assert server_info["version"] == "2.1.0"

    def test_resource_exception_handling(self) -> None:
        @get("/custom", opt={"mcp_resource": "custom_data"})
        async def custom_route() -> dict[str, Any]:
            return {"custom": "data"}

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[custom_route])
        client = TestClient(app=app)

        result = _rpc(client, "resources/read", {"uri": "litestar://custom_data"})
        contents = result["result"]["contents"]
        assert contents[0]["uri"] == "litestar://custom_data"

    def test_custom_resource_access(self) -> None:
        @get("/custom", opt={"mcp_resource": "custom_data"})
        async def custom_route() -> dict[str, Any]:
            return {"custom": "data", "timestamp": "2024-01-01", "version": "1.0"}

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[custom_route])
        client = TestClient(app=app)

        result = _rpc(client, "resources/read", {"uri": "litestar://custom_data"})
        contents = result["result"]["contents"]
        assert contents[0]["uri"] == "litestar://custom_data"
        parsed = json.loads(contents[0]["text"])
        assert parsed["custom"] == "data"
        assert parsed["timestamp"] == "2024-01-01"
        assert parsed["version"] == "1.0"

    def test_plugin_coverage_gaps(self) -> None:
        plugin = LitestarMCP()

        @get("/nested-tool", opt={"mcp_tool": "nested_tool"})
        async def nested_tool() -> dict[str, Any]:
            return {"result": "nested"}

        class MockContainer:
            route_handlers = [nested_tool]

        plugin._discover_mcp_routes([MockContainer()])
        assert "nested_tool" in plugin.discovered_tools

    def test_automatic_schema_generation(self) -> None:
        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users(limit: int = 10, active: bool = True) -> list[dict[str, Any]]:
            """Get users with pagination and filtering."""
            return [{"id": 1, "name": "Alice", "active": active}][:limit]

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[get_users])
        client = TestClient(app=app)

        result = _rpc(client, "tools/list")
        tool = next(t for t in result["result"]["tools"] if t["name"] == "list_users")
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert schema["properties"]["limit"]["type"] == "integer"
        assert schema["properties"]["active"]["type"] == "boolean"
        required = schema.get("required", [])
        assert "limit" not in required
        assert "active" not in required

    def test_decorator_based_discovery(self) -> None:
        from litestar_mcp import mcp_resource, mcp_tool

        @get("/decorator-tool")
        @mcp_tool(name="decorator_tool")
        async def decorator_tool(message: str) -> dict[str, str]:
            """A tool marked with decorator."""
            return {"message": f"Processed: {message}"}

        @get("/decorator-resource")
        @mcp_resource(name="decorator_resource")
        async def decorator_resource() -> dict[str, Any]:
            """A resource marked with decorator."""
            return {"config": "value", "enabled": True}

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[decorator_tool, decorator_resource])

        assert "decorator_tool" in plugin.discovered_tools
        assert "decorator_resource" in plugin.discovered_resources

        client = TestClient(app=app)

        # Tool execution via JSON-RPC
        result = _rpc(client, "tools/call", {"name": "decorator_tool", "arguments": {"message": "test"}})
        parsed = json.loads(result["result"]["content"][0]["text"])
        assert parsed["message"] == "Processed: test"

        # Resource read via JSON-RPC
        result = _rpc(client, "resources/read", {"uri": "litestar://decorator_resource"})
        contents = result["result"]["contents"]
        parsed = json.loads(contents[0]["text"])
        assert parsed["config"] == "value"
        assert parsed["enabled"] is True
