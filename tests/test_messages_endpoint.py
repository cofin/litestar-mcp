"""Tests for /mcp/messages unified MCP endpoint."""

from __future__ import annotations

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


class TestMessagesEndpoint:
    """Test suite for /mcp/messages endpoint."""

    def test_messages_tools_list(self) -> None:
        """Test messages endpoint with tools/list method."""

        @get("/test", opt={"mcp_tool": "test_tool"})
        def test_handler(name: str) -> dict[str, str]:
            """Test tool."""
            return {"result": f"Hello, {name}"}

        app = Litestar(
            route_handlers=[test_handler],
            plugins=[LitestarMCP()],
        )

        with TestClient(app=app) as client:
            response = client.post("/mcp/messages", json={"method": "tools/list"})
            assert response.status_code == 200
            data = response.json()
            assert "result" in data
            assert "tools" in data["result"]
            assert len(data["result"]["tools"]) == 1
            assert data["result"]["tools"][0]["name"] == "test_tool"

    def test_messages_tools_call(self) -> None:
        """Test messages endpoint with tools/call method."""

        @get("/greet", opt={"mcp_tool": "greeter"})
        def greet_handler(name: str) -> dict[str, str]:
            """Greet a user."""
            return {"greeting": f"Hello, {name}!"}

        app = Litestar(
            route_handlers=[greet_handler],
            plugins=[LitestarMCP()],
        )

        with TestClient(app=app) as client:
            response = client.post(
                "/mcp/messages",
                json={"method": "tools/call", "params": {"name": "greeter", "arguments": {"name": "Alice"}}},
            )
            assert response.status_code == 200
            data = response.json()
            assert "result" in data
            assert "content" in data["result"]

    def test_messages_resources_list(self) -> None:
        """Test messages endpoint with resources/list method."""

        @get("/config", opt={"mcp_resource": "app_config"})
        def config_handler() -> dict[str, Any]:
            """Application configuration."""
            return {"debug": True}

        app = Litestar(
            route_handlers=[config_handler],
            plugins=[LitestarMCP()],
        )

        with TestClient(app=app) as client:
            response = client.post("/mcp/messages", json={"method": "resources/list"})
            assert response.status_code == 200
            data = response.json()
            assert "result" in data
            assert "resources" in data["result"]
            resource_names = [r["name"] for r in data["result"]["resources"]]
            assert "app_config" in resource_names
            assert "openapi" in resource_names

    def test_messages_resources_read(self) -> None:
        """Test messages endpoint with resources/read method."""

        @get("/status", opt={"mcp_resource": "status"})
        def status_handler() -> dict[str, str]:
            """Status resource."""
            return {"status": "running"}

        app = Litestar(
            route_handlers=[status_handler],
            plugins=[LitestarMCP()],
        )

        with TestClient(app=app) as client:
            response = client.post(
                "/mcp/messages",
                json={"method": "resources/read", "params": {"uri": "litestar://status"}},
            )
            assert response.status_code == 200
            data = response.json()
            assert "result" in data
            assert "contents" in data["result"]

    def test_messages_openapi_resource(self) -> None:
        """Test reading OpenAPI schema via messages endpoint."""
        app = Litestar(route_handlers=[], plugins=[LitestarMCP()])

        with TestClient(app=app) as client:
            response = client.post(
                "/mcp/messages",
                json={"method": "resources/read", "params": {"uri": "litestar://openapi"}},
            )
            assert response.status_code == 200
            data = response.json()
            assert "result" in data
            assert "contents" in data["result"]
            assert data["result"]["contents"][0]["uri"] == "litestar://openapi"

    def test_messages_unknown_method(self) -> None:
        """Test messages endpoint with unknown method."""
        app = Litestar(route_handlers=[], plugins=[LitestarMCP()])

        with TestClient(app=app) as client:
            response = client.post("/mcp/messages", json={"method": "unknown/method"})
            assert response.status_code == 404

    def test_messages_tool_not_found(self) -> None:
        """Test messages endpoint with non-existent tool."""
        app = Litestar(route_handlers=[], plugins=[LitestarMCP()])

        with TestClient(app=app) as client:
            response = client.post(
                "/mcp/messages",
                json={"method": "tools/call", "params": {"name": "nonexistent", "arguments": {}}},
            )
            assert response.status_code == 404

    def test_messages_resource_not_found(self) -> None:
        """Test messages endpoint with non-existent resource."""
        app = Litestar(route_handlers=[], plugins=[LitestarMCP()])

        with TestClient(app=app) as client:
            response = client.post(
                "/mcp/messages",
                json={"method": "resources/read", "params": {"uri": "litestar://nonexistent"}},
            )
            assert response.status_code == 404

    def test_messages_capabilities_include_transports(self) -> None:
        """Test that server capabilities advertise transport support."""
        app = Litestar(route_handlers=[], plugins=[LitestarMCP()])

        with TestClient(app=app) as client:
            response = client.get("/mcp/")
            assert response.status_code == 200
            data = response.json()
            assert "capabilities" in data
            assert "transports" in data["capabilities"]
            assert "http" in data["capabilities"]["transports"]
            assert "sse" in data["capabilities"]["transports"]
