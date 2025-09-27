"""Tests for LitestarMCP integration."""

from __future__ import annotations

from typing import Any

from litestar import Litestar, get, post
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


class TestLitestarMCP:
    """Test suite for LitestarMCP."""

    def test_plugin_initialization_default(self) -> None:
        """Test plugin initialization with default values."""
        plugin = LitestarMCP()
        assert plugin.config.base_path == "/mcp"

    def test_plugin_initialization_custom(self) -> None:
        """Test plugin initialization with custom values."""
        from litestar_mcp.config import MCPConfig

        config = MCPConfig(base_path="/api/mcp")
        plugin = LitestarMCP(config)
        assert plugin.config.base_path == "/api/mcp"

    def test_plugin_discovers_mcp_routes(self) -> None:
        """Test that plugin discovers routes marked for MCP exposure.

        Tests the plugin's ability to automatically discover routes that have
        MCP opt metadata for both tools and resources.
        """

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
        """Test that MCP endpoints are accessible."""

        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[get_users])

        client = TestClient(app=app)

        response = client.get("/mcp/")
        assert response.status_code == 200
        data = response.json()
        assert "server_name" in data
        assert "capabilities" in data
        assert data["discovered"]["tools"] == 1

        response = client.get("/mcp/tools")
        assert response.status_code == 200
        data = response.json()
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "list_users"

        response = client.get("/mcp/resources")
        assert response.status_code == 200
        data = response.json()
        assert len(data["resources"]) == 1
        assert data["resources"][0]["name"] == "openapi"

    def test_openapi_resource_access(self) -> None:
        """Test accessing the built-in OpenAPI resource."""
        app = Litestar(plugins=[LitestarMCP()])
        client = TestClient(app=app)

        response = client.get("/mcp/resources/openapi")
        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert data["content"]["uri"] == "litestar://openapi"

    def test_tool_execution_real(self) -> None:
        """Test real tool execution."""

        @post("/analyze", opt={"mcp_tool": "analyze_data"})
        async def analyze(data: dict[str, Any]) -> dict[str, Any]:
            return {"result": "analyzed", "input": data}

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[analyze])

        client = TestClient(app=app)

        test_data = {"test": "data", "count": 42}
        response = client.post("/mcp/tools/analyze_data", json={"arguments": {"data": test_data}})
        assert response.status_code == 200
        data = response.json()
        assert "content" in data

        # Verify we get real execution results, not mock
        import json

        result_text = data["content"][0]["text"]
        result = json.loads(result_text)
        assert result["result"] == "analyzed"
        assert result["input"] == test_data

    def test_error_handling(self) -> None:
        """Test error handling for missing resources and tools."""
        app = Litestar(plugins=[LitestarMCP()])
        client = TestClient(app=app)

        response = client.get("/mcp/resources/nonexistent")
        assert response.status_code == 404

        response = client.post("/mcp/tools/nonexistent", json={"arguments": {}})
        assert response.status_code == 404

    def test_openapi_integration(self) -> None:
        """Test that server info uses OpenAPI configuration."""
        from litestar.openapi.config import OpenAPIConfig

        app = Litestar(plugins=[LitestarMCP()], openapi_config=OpenAPIConfig(title="My Custom API", version="2.1.0"))
        client = TestClient(app=app)

        response = client.get("/mcp/")
        assert response.status_code == 200
        data = response.json()
        assert data["server_name"] == "My Custom API"
        assert data["server_version"] == "2.1.0"

    def test_resource_exception_handling(self) -> None:
        """Test resource access exception handling."""

        @get("/custom", opt={"mcp_resource": "custom_data"})
        async def custom_route() -> dict[str, Any]:
            return {"custom": "data"}

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[custom_route])
        client = TestClient(app=app)

        # Test successful resource access first
        response = client.get("/mcp/resources/custom_data")
        assert response.status_code == 200
        data = response.json()
        assert data["content"]["uri"] == "litestar://custom_data"

    def test_custom_resource_access(self) -> None:
        """Test accessing a custom discovered resource."""

        @get("/custom", opt={"mcp_resource": "custom_data"})
        async def custom_route() -> dict[str, Any]:
            return {"custom": "data", "timestamp": "2024-01-01", "version": "1.0"}

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[custom_route])
        client = TestClient(app=app)

        response = client.get("/mcp/resources/custom_data")
        assert response.status_code == 200
        data = response.json()
        assert data["content"]["uri"] == "litestar://custom_data"

        # Verify we get real resource data, not mock
        import json

        result_text = data["content"]["text"]
        result = json.loads(result_text)
        assert result["custom"] == "data"
        assert result["timestamp"] == "2024-01-01"
        assert result["version"] == "1.0"

    def test_plugin_coverage_gaps(self) -> None:
        """Test remaining coverage gaps in plugin.py."""

        # Test the recursive discovery by manually calling the method with nested structure
        plugin = LitestarMCP()

        @get("/nested-tool", opt={"mcp_tool": "nested_tool"})
        async def nested_tool() -> dict[str, Any]:
            return {"result": "nested"}

        # Mock a container with route_handlers attribute
        class MockContainer:
            route_handlers = [nested_tool]

        # Test the recursive discovery
        plugin._discover_mcp_routes([MockContainer()])

        # Should have discovered the nested tool
        assert "nested_tool" in plugin.discovered_tools

    def test_automatic_schema_generation(self) -> None:
        """Test that tools now have automatically generated schemas."""

        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users(limit: int = 10, active: bool = True) -> list[dict[str, Any]]:
            """Get users with pagination and filtering."""
            return [{"id": 1, "name": "Alice", "active": active}][:limit]

        app = Litestar(plugins=[LitestarMCP()], route_handlers=[get_users])
        client = TestClient(app=app)

        response = client.get("/mcp/tools")
        assert response.status_code == 200
        data = response.json()

        # Find our tool
        tool = next(t for t in data["tools"] if t["name"] == "list_users")

        # Verify it has a real schema, not a placeholder
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema

        # Check properties
        properties = schema["properties"]
        assert "limit" in properties
        assert "active" in properties

        assert properties["limit"]["type"] == "integer"
        assert properties["active"]["type"] == "boolean"

        # Check required fields (both have defaults, so neither should be required)
        required = schema.get("required", [])
        assert "limit" not in required
        assert "active" not in required

    def test_decorator_based_discovery(self) -> None:
        """Test that decorator-based tools and resources are discovered."""
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

        # Check discovery
        assert "decorator_tool" in plugin.discovered_tools
        assert "decorator_resource" in plugin.discovered_resources

        # Test via HTTP endpoints
        client = TestClient(app=app)

        # Test tool execution
        response = client.post("/mcp/tools/decorator_tool", json={"arguments": {"message": "test"}})
        assert response.status_code == 200
        data = response.json()
        import json

        result = json.loads(data["content"][0]["text"])
        assert result["message"] == "Processed: test"

        # Test resource access
        response = client.get("/mcp/resources/decorator_resource")
        assert response.status_code == 200
        data = response.json()
        result = json.loads(data["content"]["text"])
        assert result["config"] == "value"
        assert result["enabled"] is True
