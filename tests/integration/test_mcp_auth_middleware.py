"""Ch3 Phase 1 red-phase integration tests.

Exercises the Ch3 public contract: ``DefineMiddleware(MCPAuthBackend, ...)``
as the auth enforcement point, ``MCPAuthConfig`` collapsed to pure metadata
for the ``.well-known`` manifest, and the ``initialize``/``ping`` exemption
removed so clients must present a token before any JSON-RPC call.

These tests will fail until Phase 2 (module reorg) + Phase 3 (backend impl)
+ Phase 4 (routes.py cleanup) land.
"""

from __future__ import annotations

from typing import Any

from litestar import Litestar, get
from litestar.middleware import DefineMiddleware
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthBackend, MCPAuthConfig
from litestar_mcp.decorators import mcp_tool
from tests.integration._auth import (
    AUDIENCE,
    FORGED_TOKEN,
    ISSUER,
    VALID_TOKEN,
    AuthenticatedUser,
    bearer_token_validator,
)


async def _user_resolver(claims: dict[str, Any], _app: Any) -> AuthenticatedUser:
    scopes = claims.get("scopes") or []
    if not isinstance(scopes, list):
        scopes = []
    return AuthenticatedUser(sub=str(claims.get("sub", "")), scopes=tuple(str(s) for s in scopes))


def _build_app_with_backend() -> Litestar:
    """App with the built-in MCPAuthBackend installed as middleware."""

    @get("/echo-user", sync_to_thread=False)
    @mcp_tool(name="echo_user")
    def echo_user(request: Any) -> dict[str, Any]:
        """Return the authenticated user's sub claim."""
        user = request.user
        return {"sub": getattr(user, "sub", None)}

    metadata = MCPAuthConfig(issuer=ISSUER, audience=AUDIENCE, scopes={"mcp:read": "Read MCP tools"})
    return Litestar(
        route_handlers=[echo_user],
        middleware=[
            DefineMiddleware(
                MCPAuthBackend,
                token_validator=bearer_token_validator,
                user_resolver=_user_resolver,
            ),
        ],
        plugins=[LitestarMCP(MCPConfig(auth=metadata))],
    )


def _initialize(client: TestClient[Any], headers: dict[str, str] | None = None) -> Any:
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
        headers=headers or {},
    )


class TestMCPAuthBackendMiddleware:
    """Integration tests for MCPAuthBackend installed as middleware on /mcp."""

    def test_well_known_paths_unauthenticated(self) -> None:
        """.well-known routes are exempt via opt={'exclude_from_auth': True}."""
        with TestClient(app=_build_app_with_backend()) as client:
            for path in (
                "/.well-known/oauth-protected-resource",
                "/.well-known/agent-card.json",
                "/.well-known/mcp-server.json",
            ):
                resp = client.get(path)
                assert resp.status_code == 200, f"{path} should be unauthenticated; got {resp.status_code}"

    def test_initialize_requires_token(self) -> None:
        """Ch3 removes the initialize/ping auth exemption. Clients MUST present a token."""
        with TestClient(app=_build_app_with_backend()) as client:
            # Without a token → 401
            resp = _initialize(client)
            assert resp.status_code == 401

            # With a valid token → 200
            resp = _initialize(client, headers={"Authorization": f"Bearer {VALID_TOKEN}"})
            assert resp.status_code == 200

    def test_invalid_token_rejected(self) -> None:
        """Invalid bearer tokens return 401 with WWW-Authenticate: Bearer."""
        with TestClient(app=_build_app_with_backend()) as client:
            resp = _initialize(client, headers={"Authorization": f"Bearer {FORGED_TOKEN}"})
            assert resp.status_code == 401
            assert "bearer" in resp.headers.get("www-authenticate", "").lower()

    def test_tool_call_uses_request_user(self) -> None:
        """Tool handlers see ``request.user`` populated by the middleware."""
        headers = {"Authorization": f"Bearer {VALID_TOKEN}"}
        with TestClient(app=_build_app_with_backend()) as client:
            init = _initialize(client, headers=headers)
            assert init.status_code == 200
            session_id = init.headers.get("mcp-session-id", "")
            assert session_id

            notif_headers = {**headers, "Mcp-Session-Id": session_id}
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=notif_headers,
            )

            call_headers = {**headers, "Mcp-Session-Id": session_id}
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "echo_user", "arguments": {}},
                },
                headers=call_headers,
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "result" in body, body
            content = body["result"]["content"][0]["text"]
            assert '"sub": "integration-user"' in content or '"sub":"integration-user"' in content


class TestBYOAuthMiddlewareCompatibility:
    """DMA pattern: app uses its own AbstractAuthenticationMiddleware; no MCPAuthBackend."""

    def test_custom_middleware_populates_request_user(self) -> None:
        """Apps with their own auth middleware inherit user into MCP tool calls — no MCPAuthBackend needed."""
        from litestar.exceptions import NotAuthorizedException
        from litestar.middleware.authentication import (
            AbstractAuthenticationMiddleware,
            AuthenticationResult,
        )

        missing_msg = "missing bearer"
        invalid_msg = "invalid bearer"

        class CustomAuth(AbstractAuthenticationMiddleware):
            async def authenticate_request(self, connection: Any) -> AuthenticationResult:
                header = connection.headers.get("authorization", "")
                if not header.startswith("Bearer "):
                    raise NotAuthorizedException(missing_msg)
                claims = await bearer_token_validator(header[7:])
                if claims is None:
                    raise NotAuthorizedException(invalid_msg)
                user = AuthenticatedUser(sub=str(claims["sub"]), scopes=tuple(claims.get("scopes", [])))
                return AuthenticationResult(user=user, auth=claims)

        @get("/whoami", sync_to_thread=False)
        @mcp_tool(name="whoami")
        def whoami(request: Any) -> dict[str, Any]:
            return {"sub": request.user.sub}

        app = Litestar(
            route_handlers=[whoami],
            middleware=[DefineMiddleware(CustomAuth)],
            plugins=[LitestarMCP(MCPConfig())],
        )

        headers = {"Authorization": f"Bearer {VALID_TOKEN}"}
        with TestClient(app=app) as client:
            init = _initialize(client, headers=headers)
            assert init.status_code == 200
            sid = init.headers["mcp-session-id"]
            notif_headers = {**headers, "Mcp-Session-Id": sid}
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=notif_headers,
            )

            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}},
                },
                headers=notif_headers,
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "result" in body, body
            text = body["result"]["content"][0]["text"]
            assert "integration-user" in text


class TestCollapsedAuthConfigMetadata:
    """MCPAuthConfig with NO middleware installed: pure metadata for the well-known manifest."""

    def test_metadata_only_app_serves_well_known(self) -> None:
        @get("/public-tool", sync_to_thread=False)
        @mcp_tool(name="public_tool")
        def public_tool() -> dict[str, Any]:
            return {"ok": True}

        metadata = MCPAuthConfig(
            issuer="https://idp.example.com",
            audience="my-mcp",
            scopes={"mcp:read": "Read", "mcp:write": "Write"},
        )
        app = Litestar(
            route_handlers=[public_tool],
            plugins=[LitestarMCP(MCPConfig(auth=metadata))],
        )

        with TestClient(app=app) as client:
            resp = client.get("/.well-known/oauth-protected-resource")
            assert resp.status_code == 200
            body = resp.json()
            assert body["resource"] == "my-mcp"
            assert body["authorization_servers"] == ["https://idp.example.com"]
            assert set(body["scopes_supported"]) == {"mcp:read", "mcp:write"}

            # With no middleware installed, there's no enforcement → initialize succeeds.
            init = _initialize(client)
            assert init.status_code == 200
