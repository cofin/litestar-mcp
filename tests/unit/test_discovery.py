"""Tests for generated discovery artifacts."""

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.config import MCPConfig
from litestar_mcp.utils import mcp_tool


def _make_discovery_app() -> "Litestar":
    @get("/check", sync_to_thread=False)
    @mcp_tool(name="check_health")
    def check_health() -> "dict[str, str]":
        """Check service health."""
        return {"status": "ok"}

    return Litestar(route_handlers=[check_health], plugins=[LitestarMCP()])


def _make_custom_base_path_discovery_app() -> "Litestar":
    @get("/check", sync_to_thread=False)
    @mcp_tool(name="check_health")
    def check_health() -> "dict[str, str]":
        """Check service health."""
        return {"status": "ok"}

    return Litestar(
        route_handlers=[check_health],
        plugins=[LitestarMCP(MCPConfig(base_path="/api/mcp"))],
    )


def test_agent_card_endpoint_generated() -> "None":
    app = _make_discovery_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
        payload = response.json()

        assert payload["name"]
        assert payload["url"].endswith("/mcp")
        assert payload["defaultInputModes"] == ["application/json"]
        assert any(skill["id"] == "check_health" for skill in payload["skills"])


def test_experimental_mcp_server_manifest_removed() -> "None":
    app = _make_discovery_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/mcp-server.json")
        assert response.status_code == 404


def test_custom_base_path_does_not_restore_removed_manifest() -> "None":
    app = _make_custom_base_path_discovery_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/mcp-server.json")
        nested_response = client.get("/api/mcp/.well-known/mcp-server.json")

    assert response.status_code == 404
    assert nested_response.status_code in (404, 405)


def test_custom_base_path_agent_card_reports_mcp_url() -> "None":
    app = _make_custom_base_path_discovery_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/agent-card.json")
        nested_response = client.get("/api/mcp/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json()["url"].endswith("/api/mcp")
    assert nested_response.status_code in (404, 405)
