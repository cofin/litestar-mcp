"""Tests for security functionality."""

from typing import TYPE_CHECKING, Any

import pytest
from litestar import Litestar, get
from litestar.exceptions import PermissionDeniedException
from litestar.handlers.base import BaseRouteHandler
from litestar.openapi.config import OpenAPIConfig
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.security.jwt import JWTAuth, Token

try:
    from litestar.security.jwt import JWTAuth, OAuth2PasswordBearerAuth, Token

    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False

JWT_AVAILABLE = _JWT_AVAILABLE


def _rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    headers: "dict[str, str] | None" = None,
    base: str = "/mcp",
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(base, json=body, headers=headers or {})


class TestSecurity:
    """Test suite for MCP security features."""

    def test_mcp_endpoints_without_guards(self) -> None:
        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[get_users])
        client = TestClient(app=app)

        # tools/list without auth
        resp = _rpc(client, "tools/list")
        assert resp.status_code == 200
        assert "result" in resp.json()

        # tools/call without auth
        resp = _rpc(client, "tools/call", {"name": "list_users", "arguments": {}})
        assert resp.status_code == 200
        assert "result" in resp.json()

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_mcp_endpoints_with_jwt_and_guards(self) -> None:
        jwt_auth: JWTAuth[dict[str, Any], Token] = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        async def admin_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            user = connection.user
            if not user or "admin" not in user.get("roles", []):
                msg = "Admin privileges required"
                raise PermissionDeniedException(msg)

        mcp_config = MCPConfig(guards=[admin_guard])
        plugin = LitestarMCP(config=mcp_config)

        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        app = Litestar(
            plugins=[plugin],
            route_handlers=[get_users],
            on_app_init=[jwt_auth.on_app_init],
            openapi_config=OpenAPIConfig(title="Test API", version="1.0.0"),
        )

        client = TestClient(app=app)

        # Without token — 401
        resp = _rpc(client, "tools/list")
        assert resp.status_code == 401

        # Invalid token — 401
        resp = _rpc(client, "tools/list", headers={"Authorization": "Bearer invalid-token"})
        assert resp.status_code == 401

        # Valid token, wrong role — 403
        user_token = jwt_auth.create_token(identifier="user", token_extras={"roles": ["user"]})
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {user_token}"})
        assert resp.status_code == 403

        # Valid token, correct role — 200
        admin_token = jwt_auth.create_token(identifier="admin", token_extras={"roles": ["admin"]})
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200

        # Tool execution with auth
        resp = _rpc(
            client, "tools/call", {"name": "list_users", "arguments": {}},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_multiple_guards(self) -> None:
        jwt_auth: JWTAuth[dict[str, Any], Token] = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        async def role_guard(connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler) -> None:
            user = connection.user
            if not user or "mcp_user" not in user.get("roles", []):
                msg = "MCP access role required"
                raise PermissionDeniedException(msg)

        async def scope_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            user = connection.user
            if not user or "mcp:read" not in user.get("scopes", []):
                msg = "MCP read scope required"
                raise PermissionDeniedException(msg)

        mcp_config = MCPConfig(guards=[role_guard, scope_guard])
        plugin = LitestarMCP(config=mcp_config)

        @get("/data", opt={"mcp_tool": "get_data"})
        async def get_data() -> dict[str, str]:
            return {"data": "sensitive"}

        app = Litestar(
            plugins=[plugin],
            route_handlers=[get_data],
            on_app_init=[jwt_auth.on_app_init],
        )
        client = TestClient(app=app)

        wrong_token = jwt_auth.create_token(identifier="user", token_extras={"roles": ["user"], "scopes": ["read"]})
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {wrong_token}"})
        assert resp.status_code == 403

        partial_token = jwt_auth.create_token(
            identifier="user", token_extras={"roles": ["mcp_user"], "scopes": ["read"]}
        )
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {partial_token}"})
        assert resp.status_code == 403

        correct_token = jwt_auth.create_token(
            identifier="user", token_extras={"roles": ["mcp_user"], "scopes": ["mcp:read"]}
        )
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {correct_token}"})
        assert resp.status_code == 200

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_guard_only_affects_mcp_endpoints(self) -> None:
        jwt_auth = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
            exclude=["/public", "/protected"],
        )

        async def strict_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            msg = "Access denied"
            raise PermissionDeniedException(msg)

        mcp_config = MCPConfig(guards=[strict_guard])
        plugin = LitestarMCP(config=mcp_config)

        @get("/public")
        async def public_route() -> dict[str, str]:
            return {"message": "public"}

        @get("/protected", opt={"mcp_tool": "protected_tool"})
        async def protected_route() -> dict[str, str]:
            return {"message": "protected"}

        app = Litestar(
            plugins=[plugin],
            route_handlers=[public_route, protected_route],
            on_app_init=[jwt_auth.on_app_init],
        )
        client = TestClient(app=app)

        # Public route still works
        response = client.get("/public")
        assert response.status_code == 200

        # Direct access to protected route works
        response = client.get("/protected")
        assert response.status_code == 200

        # MCP POST blocked by guard
        admin_token = jwt_auth.create_token(identifier="admin", token_extras={"roles": ["admin"]})
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 403

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_custom_error_handling_in_guards(self) -> None:
        jwt_auth: JWTAuth[dict[str, Any], Token] = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        async def custom_message_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            user = connection.user
            if not user or user.get("department") != "AI":
                msg = "Only AI department personnel can access MCP tools"
                raise PermissionDeniedException(msg)

        mcp_config = MCPConfig(guards=[custom_message_guard])
        plugin = LitestarMCP(config=mcp_config)

        @get("/ai-tool", opt={"mcp_tool": "ai_processor"})
        async def ai_tool() -> dict[str, str]:
            return {"status": "processing"}

        app = Litestar(
            plugins=[plugin],
            route_handlers=[ai_tool],
            on_app_init=[jwt_auth.on_app_init],
        )
        client = TestClient(app=app)

        wrong_dept_token = jwt_auth.create_token(
            identifier="user", token_extras={"department": "HR", "roles": ["user"]}
        )
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {wrong_dept_token}"})
        assert resp.status_code == 403

        ai_dept_token = jwt_auth.create_token(identifier="user", token_extras={"department": "AI", "roles": ["user"]})
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {ai_dept_token}"})
        assert resp.status_code == 200

    def test_guard_configuration_backward_compatibility(self) -> None:
        @get("/tool", opt={"mcp_tool": "simple_tool"})
        async def simple_tool() -> dict[str, str]:
            return {"result": "success"}

        config_without_guards = MCPConfig(base_path="/api/mcp")
        plugin = LitestarMCP(config=config_without_guards)

        app = Litestar(plugins=[plugin], route_handlers=[simple_tool])
        client = TestClient(app=app)

        # tools/list via JSON-RPC at custom base path
        resp = _rpc(client, "tools/list", base="/api/mcp")
        assert resp.status_code == 200

        # tools/call via JSON-RPC
        resp = _rpc(client, "tools/call", {"name": "simple_tool", "arguments": {}}, base="/api/mcp")
        assert resp.status_code == 200

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_oauth2_password_bearer_auth_with_guards(self) -> None:
        """Test MCP endpoints with OAuth2PasswordBearerAuth — the auth backend
        used by DMA Accelerator and litestar-fullstack-spa.

        This validates the auth bridge pattern: the app uses
        OAuth2PasswordBearerAuth with a retrieve_user_handler that returns a
        user dict. Guards then check roles/permissions on that user.  MCP
        endpoints sit behind the same auth middleware, so MCP agents must
        present a valid JWT to access tools.
        """

        oauth2_auth: OAuth2PasswordBearerAuth[dict[str, Any], Token] = OAuth2PasswordBearerAuth[
            dict[str, Any], Token
        ](
            token_secret="super-secret-key-for-testing-32b!",
            token_url="/api/auth/login",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        async def requires_workspace_membership(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            """Simulates a real guard like requires_workspace_membership."""
            user = connection.user
            if not user or "workspace_member" not in user.get("roles", []):
                msg = "Workspace membership required"
                raise PermissionDeniedException(msg)

        # MCP router protected by the workspace guard
        mcp_config = MCPConfig(guards=[requires_workspace_membership])
        plugin = LitestarMCP(config=mcp_config)

        @get("/workspaces/{workspace_id:str}/databases", opt={"mcp_tool": "list_databases"})
        async def list_databases(workspace_id: str) -> list[dict[str, Any]]:
            """List databases in a workspace."""
            return [{"id": "db1", "name": "Production", "workspace_id": workspace_id}]

        app = Litestar(
            plugins=[plugin],
            route_handlers=[list_databases],
            on_app_init=[oauth2_auth.on_app_init],
            openapi_config=OpenAPIConfig(title="DMA Accelerator", version="1.0.0"),
        )
        client = TestClient(app=app)

        # ── No token → 401 ──
        resp = _rpc(client, "tools/list")
        assert resp.status_code == 401

        # ── Valid token, no workspace role → 403 ──
        basic_token = oauth2_auth.create_token(
            identifier="user@example.com",
            token_extras={"roles": ["viewer"]},
        )
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {basic_token}"})
        assert resp.status_code == 403

        # ── Valid token, workspace member → 200, can list tools ──
        member_token = oauth2_auth.create_token(
            identifier="user@example.com",
            token_extras={"roles": ["workspace_member"]},
        )
        resp = _rpc(client, "tools/list", headers={"Authorization": f"Bearer {member_token}"})
        assert resp.status_code == 200
        body = resp.json()
        tools = body["result"]["tools"]
        assert any(t["name"] == "list_databases" for t in tools)

        # ── Workspace member can execute the tool ──
        resp = _rpc(
            client,
            "tools/call",
            {"name": "list_databases", "arguments": {"workspace_id": "ws-123"}},
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body
        import json

        content = json.loads(body["result"]["content"][0]["text"])
        assert content[0]["workspace_id"] == "ws-123"

        # ── Direct route still works independently of MCP guard ──
        resp = client.get(
            "/workspaces/ws-123/databases",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 200
