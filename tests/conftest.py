"""Pytest configuration and shared fixtures."""

from __future__ import annotations

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


@pytest.fixture
def minimal_app() -> Litestar:
    """Minimal Litestar app without MCP plugin."""

    @get("/test", sync_to_thread=False)
    def test_route() -> dict[str, str]:
        return {"message": "test"}

    return Litestar(route_handlers=[test_route])


@pytest.fixture
def mcp_app() -> Litestar:
    """Litestar app with MCP plugin using opt pattern."""

    @get("/test", sync_to_thread=False)
    def test_route() -> dict[str, str]:
        return {"message": "test"}

    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    @get("/config", opt={"mcp_resource": "app_config"}, sync_to_thread=False)
    def get_config() -> dict[str, Any]:
        return {"debug": True, "version": "1.0.0"}

    return Litestar(route_handlers=[test_route, list_users, get_config], plugins=[LitestarMCP()])


@pytest.fixture
def client(mcp_app: Litestar) -> TestClient[Any]:
    """Test client for MCP-enabled app."""
    return TestClient(app=mcp_app)
