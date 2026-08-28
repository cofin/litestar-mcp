"""Tests for discovery boundaries and avoiding A2A protocol claims."""

from typing import Any

from litestar import Litestar, get
from litestar.middleware import DefineMiddleware
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthBackend, MCPAuthConfig
from litestar_mcp.utils import mcp_tool
from tests.integration._auth import AUDIENCE, ISSUER, bearer_token_validator


async def _user_resolver(claims: "dict[str, Any]", _app: "Any") -> "Any":
    """User resolver mock for testing."""
    return None


def _make_app(with_auth: "bool" = False) -> "Litestar":
    """Create a test Litestar app with the MCP plugin."""

    @get("/check", sync_to_thread=False)
    @mcp_tool(name="check_health")
    def check_health() -> "dict[str, str]":
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


def test_agent_card_is_absent_with_auth() -> "None":
    app = _make_app(with_auth=True)
    with TestClient(app=app) as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 404
