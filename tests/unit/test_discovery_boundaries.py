"""Tests for discovery boundaries and avoiding A2A protocol claims."""

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.utils import mcp_tool


def _make_app() -> "Litestar":
    """Create a test Litestar app with the MCP plugin."""

    @get("/check", sync_to_thread=False)
    @mcp_tool(name="check_health")
    def check_health() -> "dict[str, str]":
        """Check service health."""
        return {"status": "ok"}

    return Litestar(route_handlers=[check_health], plugins=[LitestarMCP(MCPConfig())])


def test_mcp_does_not_register_agent_card() -> "None":
    app = _make_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 404


def test_mcp_server_manifest_is_removed() -> "None":
    app = _make_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/mcp-server.json")
        assert response.status_code == 404
