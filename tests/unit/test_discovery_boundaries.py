"""Tests for discovery boundaries and avoiding A2A protocol claims."""

from typing import Any

from litestar import Litestar, get
from litestar.middleware import DefineMiddleware
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthBackend, MCPAuthConfig
from litestar_mcp.utils import mcp_tool
from tests.integration._auth import AUDIENCE, ISSUER, bearer_token_validator


async def _user_resolver(claims: dict[str, Any], _app: Any) -> Any:
    """User resolver mock for testing."""
    return None


def _make_app(with_auth: bool = False) -> Litestar:
    """Create a test Litestar app with the MCP plugin."""

    @get("/check", sync_to_thread=False)
    @mcp_tool(name="check_health")
    def check_health() -> dict[str, str]:
        """Check service health."""
        return {"status": "ok"}

    middleware = []
    auth_config = None
    if with_auth:
        auth_config = MCPAuthConfig(issuer=ISSUER, audience=AUDIENCE)
        middleware = [
            DefineMiddleware(
                MCPAuthBackend,
                token_validator=bearer_token_validator,
                user_resolver=_user_resolver,
            )
        ]

    return Litestar(
        route_handlers=[check_health],
        middleware=middleware,
        plugins=[LitestarMCP(MCPConfig(auth=auth_config))],
    )


def test_agent_card_does_not_claim_a2a() -> None:
    """Verify that agent-card.json does not contain A2A protocolVersion or other A2A specific fields."""
    app = _make_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
        payload = response.json()

        assert "protocolVersion" not in payload
        assert "supportsAuthenticatedExtendedCard" not in payload
        assert payload["capabilities"]["mcp"] is True
        assert payload["url"].endswith("/mcp")


def test_mcp_server_manifest_links_as_metadata() -> None:
    """Verify that mcp-server.json links the agent card as metadata, not as an A2A endpoint."""
    app = _make_app()
    with TestClient(app=app) as client:
        response = client.get("/.well-known/mcp-server.json")
        assert response.status_code == 200
        payload = response.json()

        endpoints = payload.get("endpoints", {})
        assert "agentMetadata" in endpoints
        assert "agentCard" not in endpoints
        assert endpoints["agentMetadata"].endswith("/.well-known/agent-card.json")


def test_agent_card_remains_public_with_auth() -> None:
    """Verify that agent-card.json remains public even when auth middleware is enabled."""
    app = _make_app(with_auth=True)
    with TestClient(app=app) as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
