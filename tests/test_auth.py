"""Tests for MCP OAuth 2.1 and Auth Bridge."""

from typing import Any, Optional

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthConfig, validate_bearer_token
from litestar_mcp.decorators import mcp_tool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_auth_app(
    auth_config: Optional[MCPAuthConfig] = None,
    tool_scopes: Optional[list[str]] = None,
) -> Litestar:
    """Helper to create an app with auth configuration."""

    # We need to define separate functions because the decorator captures
    # metadata at definition time and tool_scopes varies per test.
    if tool_scopes:

        @get("/users", sync_to_thread=False)
        @mcp_tool(name="list_users", scopes=tool_scopes)
        def list_users_scoped() -> list[dict[str, Any]]:
            """List all users."""
            return [{"id": 1, "name": "Alice"}]

        handlers = [list_users_scoped]
    else:

        @get("/users", sync_to_thread=False)
        @mcp_tool(name="list_users")
        def list_users() -> list[dict[str, Any]]:
            """List all users."""
            return [{"id": 1, "name": "Alice"}]

        handlers = [list_users]

    mcp_config = MCPConfig()
    if auth_config:
        mcp_config.auth = auth_config

    return Litestar(route_handlers=handlers, plugins=[LitestarMCP(mcp_config)])


def _rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    headers: "dict[str, str] | None" = None,
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


# ---------------------------------------------------------------------------
# MCPAuthConfig
# ---------------------------------------------------------------------------


class TestMCPAuthConfig:
    def test_auth_disabled_by_default(self) -> None:
        config = MCPConfig()
        assert config.auth is None

    def test_auth_config_fields(self) -> None:
        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            audience="my-mcp-server",
            scopes={"read": "Read access", "write": "Write access"},
        )
        assert auth.issuer == "https://auth.example.com"
        assert auth.audience == "my-mcp-server"
        assert auth.scopes == {"read": "Read access", "write": "Write access"}


# ---------------------------------------------------------------------------
# Protected Resource Metadata
# ---------------------------------------------------------------------------


class TestProtectedResourceMetadata:
    def test_well_known_explicit_auth_config(self) -> None:
        """Explicit MCPAuthConfig takes precedence over auto-discovery."""
        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            audience="my-mcp-server",
        )
        app = _make_auth_app(auth_config=auth)
        client = TestClient(app=app)

        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert data["resource"] == "my-mcp-server"
        assert "https://auth.example.com" in data["authorization_servers"]

    def test_well_known_no_security_returns_empty(self) -> None:
        """When no auth is configured at all, returns empty metadata."""
        app = _make_auth_app()
        client = TestClient(app=app)

        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authorization_servers"] == []

    def test_well_known_auto_discovers_from_oauth2_password_bearer(self) -> None:
        """Auto-discovers token_url from OAuth2PasswordBearerAuth via OpenAPI."""
        try:
            from litestar.security.jwt import OAuth2PasswordBearerAuth, Token
        except ImportError:
            return  # skip if JWT not available

        from litestar.openapi.config import OpenAPIConfig

        oauth2_auth: OAuth2PasswordBearerAuth[dict[str, Any], Token] = OAuth2PasswordBearerAuth[dict[str, Any], Token](
            token_secret="test-secret-key-32-bytes-long!!",
            token_url="/api/auth/login",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        @get("/test", sync_to_thread=False)
        @mcp_tool(name="test_tool")
        def test_tool() -> list[dict[str, Any]]:
            """A test tool."""
            return []

        # No MCPAuthConfig — auto-discovery should kick in
        app = Litestar(
            route_handlers=[test_tool],
            plugins=[LitestarMCP(MCPConfig())],
            on_app_init=[oauth2_auth.on_app_init],
            openapi_config=OpenAPIConfig(title="My App", version="1.0"),
        )
        client = TestClient(app=app)

        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert "/api/auth/login" in data["authorization_servers"]
        assert data["resource"] == "My App"


# ---------------------------------------------------------------------------
# Bearer Token Validation
# ---------------------------------------------------------------------------


class TestBearerTokenValidation:
    def test_validate_bearer_token_calls_validator(self) -> None:
        calls: list[str] = []

        async def my_validator(token: str) -> Optional[dict[str, Any]]:
            calls.append(token)
            if token == "valid":
                return {"sub": "user1", "scopes": ["read"]}
            return None

        auth = MCPAuthConfig(token_validator=my_validator)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(validate_bearer_token("valid", auth))
        assert result is not None
        assert result["sub"] == "user1"
        assert calls == ["valid"]

    def test_validate_bearer_token_invalid(self) -> None:
        async def my_validator(token: str) -> Optional[dict[str, Any]]:
            return None

        auth = MCPAuthConfig(token_validator=my_validator)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(validate_bearer_token("bad", auth))
        assert result is None


# ---------------------------------------------------------------------------
# Auth enforcement on MCP endpoint
# ---------------------------------------------------------------------------


class TestAuthEnforcement:
    def test_auth_required_rejects_no_token(self) -> None:
        async def validator(token: str) -> Optional[dict[str, Any]]:
            if token == "good":
                return {"sub": "user1"}
            return None

        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            token_validator=validator,
        )
        app = _make_auth_app(auth_config=auth)
        client = TestClient(app=app)

        resp = _rpc(client, "tools/list")
        assert resp.status_code == 401

    def test_auth_required_accepts_valid_token(self) -> None:
        async def validator(token: str) -> Optional[dict[str, Any]]:
            if token == "good":
                return {"sub": "user1"}
            return None

        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            token_validator=validator,
        )
        app = _make_auth_app(auth_config=auth)
        client = TestClient(app=app)

        resp = _rpc(client, "tools/list", headers={"Authorization": "Bearer good"})
        assert resp.status_code == 200

    def test_auth_required_rejects_invalid_token(self) -> None:
        async def validator(token: str) -> Optional[dict[str, Any]]:
            return None

        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            token_validator=validator,
        )
        app = _make_auth_app(auth_config=auth)
        client = TestClient(app=app)

        resp = _rpc(client, "tools/list", headers={"Authorization": "Bearer bad"})
        assert resp.status_code == 401

    def test_initialize_exempt_from_auth(self) -> None:
        async def validator(token: str) -> Optional[dict[str, Any]]:
            return None  # always reject

        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            token_validator=validator,
        )
        app = _make_auth_app(auth_config=auth)
        client = TestClient(app=app)

        # Initialize should work even without token
        resp = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Per-tool scope challenges
# ---------------------------------------------------------------------------


class TestPerToolScopes:
    def test_tool_with_required_scope_allowed(self) -> None:
        async def validator(token: str) -> Optional[dict[str, Any]]:
            return {"sub": "user1", "scopes": ["users:read"]}

        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            token_validator=validator,
        )
        app = _make_auth_app(auth_config=auth, tool_scopes=["users:read"])
        client = TestClient(app=app)

        resp = _rpc(
            client,
            "tools/call",
            {"name": "list_users", "arguments": {}},
            headers={"Authorization": "Bearer token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body

    def test_tool_with_missing_scope_rejected(self) -> None:
        async def validator(token: str) -> Optional[dict[str, Any]]:
            return {"sub": "user1", "scopes": ["other:scope"]}

        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            token_validator=validator,
        )
        app = _make_auth_app(auth_config=auth, tool_scopes=["users:read"])
        client = TestClient(app=app)

        resp = _rpc(
            client,
            "tools/call",
            {"name": "list_users", "arguments": {}},
            headers={"Authorization": "Bearer token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == -32602  # INVALID_PARAMS (insufficient scope)
