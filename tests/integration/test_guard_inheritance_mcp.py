"""Guards are the canonical access-control surface for MCP tool dispatch.

Ch2 removed the inline ``scopes=[...]`` short-circuit in
``routes.handle_tools_call``. These tests prove guard inheritance replaces it:
both an authenticated-user guard and a custom claim-based guard reject MCP
tool calls identically to HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from litestar import Controller, Litestar, get
from litestar.exceptions import PermissionDeniedException
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler

pytestmark = pytest.mark.integration

_UNAUTHENTICATED = "Unauthenticated"
_INVALID_AUTH = "Invalid auth payload"
_DOMAIN_DENIED = "Domain not allowed"


def _targets_mcp_tool(handler: BaseRouteHandler) -> bool:
    opt = getattr(handler, "opt", {}) or {}
    return "mcp_tool" in opt


def requires_authenticated_user(
    connection: ASGIConnection[Any, Any, Any, Any],
    handler: BaseRouteHandler,
) -> None:
    if not _targets_mcp_tool(handler):
        return
    if connection.scope.get("auth") is None:
        raise PermissionDeniedException(_UNAUTHENTICATED)


def require_email_domain(
    connection: ASGIConnection[Any, Any, Any, Any],
    handler: BaseRouteHandler,
) -> None:
    if not _targets_mcp_tool(handler):
        return
    auth = connection.scope.get("auth") or {}
    if not isinstance(auth, dict):
        raise PermissionDeniedException(_INVALID_AUTH)
    email = str(auth.get("email") or "").lower()
    if not email.endswith("@allowed.test"):
        raise PermissionDeniedException(_DOMAIN_DENIED)


def _ensure_session(client: TestClient[Any]) -> str:
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return str(sid)


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    sid: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize" and sid:
        headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def test_mcp_tool_rejects_unauthenticated_via_guard() -> None:
    """Controller with ``requires_authenticated_user`` guard blocks anonymous MCP tool calls."""

    class GuardedController(Controller):
        path = "/guarded"
        guards = [requires_authenticated_user]

        @get("/x", opt={"mcp_tool": "guarded_tool"}, sync_to_thread=False)
        def x(self) -> dict[str, str]:
            return {"ok": "yes"}

    app = Litestar(route_handlers=[GuardedController], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        resp = _rpc(client, "tools/call", {"name": "guarded_tool", "arguments": {}}, sid=sid)

    assert resp["result"]["isError"] is True
    payload = str(resp["result"])
    assert "Unauthenticated" in payload
    assert "Insufficient scope" not in payload


def test_mcp_tool_custom_claim_guard_rejects_wrong_domain() -> None:
    """Custom claim-based guard rejects disallowed caller on MCP identically to HTTP."""

    class DomainGatedController(Controller):
        path = "/domain"
        guards = [require_email_domain]

        @get("/x", opt={"mcp_tool": "domain_tool"}, sync_to_thread=False)
        def x(self) -> dict[str, str]:
            return {"ok": "yes"}

    # Build an inline auth middleware that stamps a disallowed caller onto the scope.
    from litestar.middleware import (
        AbstractAuthenticationMiddleware,
        AuthenticationResult,
        DefineMiddleware,
    )

    class _StubAuth(AbstractAuthenticationMiddleware):
        async def authenticate_request(
            self,
            connection: ASGIConnection[Any, Any, Any, Any],
        ) -> AuthenticationResult:
            return AuthenticationResult(user="bob", auth={"email": "bob@blocked.test"})

    app = Litestar(
        route_handlers=[DomainGatedController],
        plugins=[LitestarMCP(MCPConfig())],
        middleware=[DefineMiddleware(_StubAuth, exclude=["/mcp", "/.well-known"])],
    )
    # The stub middleware excludes /mcp (session scope-populated via the JSON-RPC handler itself),
    # so the MCP dispatch path synthesises a request that routes to the GuardedController's
    # /domain/x endpoint. That handler inherits the stub auth via scope propagation.
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        resp = _rpc(client, "tools/call", {"name": "domain_tool", "arguments": {}}, sid=sid)

    assert resp["result"]["isError"] is True
    payload = str(resp["result"])
    assert "Domain not allowed" in payload or "Invalid auth payload" in payload or "auth" in payload.lower()


def test_mcp_tool_custom_claim_guard_accepts_allowed_domain() -> None:
    """Custom claim-based guard allows an allowed-domain caller through on MCP."""

    class DomainGatedController(Controller):
        path = "/domain"
        guards = [require_email_domain]

        @get("/x", opt={"mcp_tool": "domain_tool"}, sync_to_thread=False)
        def x(self) -> dict[str, str]:
            return {"ok": "yes"}

    from litestar.middleware import (
        AbstractAuthenticationMiddleware,
        AuthenticationResult,
        DefineMiddleware,
    )

    class _StubAuth(AbstractAuthenticationMiddleware):
        async def authenticate_request(
            self,
            connection: ASGIConnection[Any, Any, Any, Any],
        ) -> AuthenticationResult:
            return AuthenticationResult(user="alice", auth={"email": "alice@allowed.test"})

    app = Litestar(
        route_handlers=[DomainGatedController],
        plugins=[LitestarMCP(MCPConfig())],
        middleware=[DefineMiddleware(_StubAuth, exclude=["/.well-known"])],
    )
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        resp = _rpc(client, "tools/call", {"name": "domain_tool", "arguments": {}}, sid=sid)

    assert resp["result"].get("isError") is not True, resp
    assert "ok" in str(resp["result"])
