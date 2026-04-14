"""Tests for generated discovery artifacts."""

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.decorators import mcp_tool


def _make_discovery_app() -> Litestar:
    @get("/check", sync_to_thread=False)
    @mcp_tool(name="check_health")
    def check_health() -> dict[str, str]:
        """Check service health."""
        return {"status": "ok"}

    return Litestar(route_handlers=[check_health], plugins=[LitestarMCP()])


def test_agent_card_endpoint_generated() -> None:
    app = _make_discovery_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
        payload = response.json()

        assert payload["name"]
        assert payload["url"].endswith("/mcp")
        assert payload["defaultInputModes"] == ["application/json"]
        assert any(skill["id"] == "check_health" for skill in payload["skills"])


def test_experimental_mcp_server_manifest_generated() -> None:
    app = _make_discovery_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/mcp-server.json")
        assert response.status_code == 200
        payload = response.json()

        assert payload["experimental"] is True
        assert payload["protocolVersion"] == "2025-11-25"
        assert payload["endpoints"]["mcp"].endswith("/mcp")
        assert "tools" in payload

