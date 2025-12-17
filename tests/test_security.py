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
    from litestar.security.jwt import JWTAuth, Token

    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False

JWT_AVAILABLE = _JWT_AVAILABLE


class TestSecurity:
    """Test suite for MCP security features."""

    def test_mcp_endpoints_without_guards(self) -> None:
        """Test that MCP endpoints work without guards (default behavior)."""

        @get("/users", opt={"mcp_tool": "list_users"})
        async def get_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        plugin = LitestarMCP()
        app = Litestar(plugins=[plugin], route_handlers=[get_users])
        client = TestClient(app=app)

        response = client.get("/mcp/tools")
        assert response.status_code == 200

        response = client.post("/mcp/tools/list_users", json={"arguments": {}})
        assert response.status_code == 200

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_mcp_endpoints_with_jwt_and_guards(self) -> None:
        """Test MCP endpoints protected with JWT authentication and guards."""

        jwt_auth: JWTAuth[dict[str, Any], Token] = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        async def admin_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            """Guard that requires admin role."""
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

        response = client.get("/mcp/tools")
        assert response.status_code == 401

        response = client.get("/mcp/tools", headers={"Authorization": "Bearer invalid-token"})
        assert response.status_code == 401

        user_token = jwt_auth.create_token(identifier="user", token_extras={"roles": ["user"]})
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {user_token}"})
        assert response.status_code == 403

        admin_token = jwt_auth.create_token(identifier="admin", token_extras={"roles": ["admin"]})
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {admin_token}"})
        assert response.status_code == 200

        response = client.post(
            "/mcp/tools/list_users",
            json={"arguments": {}},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_multiple_guards(self) -> None:
        """Test MCP endpoints with multiple guards."""

        jwt_auth: JWTAuth[dict[str, Any], Token] = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        async def role_guard(connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler) -> None:
            """Guard that checks for specific role."""
            user = connection.user
            if not user or "mcp_user" not in user.get("roles", []):
                msg = "MCP access role required"
                raise PermissionDeniedException(msg)

        async def scope_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            """Guard that checks for specific scope."""
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
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {wrong_token}"})
        assert response.status_code == 403

        partial_token = jwt_auth.create_token(
            identifier="user", token_extras={"roles": ["mcp_user"], "scopes": ["read"]}
        )
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {partial_token}"})
        assert response.status_code == 403

        correct_token = jwt_auth.create_token(
            identifier="user", token_extras={"roles": ["mcp_user"], "scopes": ["mcp:read"]}
        )
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {correct_token}"})
        assert response.status_code == 200

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_guard_only_affects_mcp_endpoints(self) -> None:
        """Test that guards only affect MCP endpoints, not regular app routes."""

        jwt_auth = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
            exclude=["/public", "/protected"],
        )

        async def strict_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            """Very strict guard that always denies access."""
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

        response = client.get("/public")
        assert response.status_code == 200

        response = client.get("/protected")
        assert response.status_code == 200

        admin_token = jwt_auth.create_token(identifier="admin", token_extras={"roles": ["admin"]})
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {admin_token}"})
        assert response.status_code == 403

    @pytest.mark.skipif(not JWT_AVAILABLE, reason="JWT auth not available")
    def test_custom_error_handling_in_guards(self) -> None:
        """Test custom error messages from guards."""

        jwt_auth: JWTAuth[dict[str, Any], Token] = JWTAuth[dict[str, Any], Token](
            token_secret="super-secret-key-for-testing",
            retrieve_user_handler=lambda token, _: token.extras,
        )

        async def custom_message_guard(
            connection: "ASGIConnection[Any, Any, Any, Any]", route_handler: BaseRouteHandler
        ) -> None:
            """Guard with custom error message."""
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
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {wrong_dept_token}"})
        assert response.status_code == 403

        ai_dept_token = jwt_auth.create_token(identifier="user", token_extras={"department": "AI", "roles": ["user"]})
        response = client.get("/mcp/tools", headers={"Authorization": f"Bearer {ai_dept_token}"})
        assert response.status_code == 200

    def test_guard_configuration_backward_compatibility(self) -> None:
        """Test that not specifying guards maintains backward compatibility."""

        @get("/tool", opt={"mcp_tool": "simple_tool"})
        async def simple_tool() -> dict[str, str]:
            return {"result": "success"}

        config_without_guards = MCPConfig(base_path="/api/mcp")
        plugin = LitestarMCP(config=config_without_guards)

        app = Litestar(plugins=[plugin], route_handlers=[simple_tool])
        client = TestClient(app=app)

        response = client.get("/api/mcp/tools")
        assert response.status_code == 200

        response = client.post("/api/mcp/tools/simple_tool", json={"arguments": {}})
        assert response.status_code == 200
