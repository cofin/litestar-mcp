"""Guard inheritance for MCP tool invocations.

Ch1 of the native-request-pipeline PRD: ``execute_tool`` must honor guards
declared at app / router / controller / route scope, matching Litestar's own
``resolve_guards()`` traversal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from litestar import Controller, Litestar, Router, get
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler

pytestmark = pytest.mark.unit


def _targets_mcp_tool(handler: BaseRouteHandler) -> bool:
    """Only fire the guard for MCP-marked tool handlers, not for /mcp itself."""
    opt = getattr(handler, "opt", {}) or {}
    return "mcp_tool" in opt


def _ensure_session(client: TestClient[Any]) -> str:
    sid = getattr(client, "_mcp_session", None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
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
    client._mcp_session = sid  # type: ignore[attr-defined]
    return str(sid)


def _rpc(client: TestClient[Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def _call_tool(client: TestClient[Any], name: str) -> dict[str, Any]:
    return _rpc(client, "tools/call", {"name": name, "arguments": {}})


_APP_BLOCKED = "app-guard blocked"
_ROUTER_BLOCKED = "router-guard blocked"
_CONTROLLER_BLOCKED = "controller-guard blocked"
_ROUTE_BLOCKED = "route-guard blocked"


async def _app_guard(connection: ASGIConnection[Any, Any, Any, Any], handler: BaseRouteHandler) -> None:
    if not _targets_mcp_tool(handler):
        return
    if getattr(connection.app.state, "block_app", False):
        raise NotAuthorizedException(_APP_BLOCKED)
    getattr(connection.app.state, "order", []).append("app")


def _router_guard(connection: ASGIConnection[Any, Any, Any, Any], handler: BaseRouteHandler) -> None:
    if not _targets_mcp_tool(handler):
        return
    if getattr(connection.app.state, "block_router", False):
        raise NotAuthorizedException(_ROUTER_BLOCKED)
    getattr(connection.app.state, "order", []).append("router")


async def _controller_guard(connection: ASGIConnection[Any, Any, Any, Any], handler: BaseRouteHandler) -> None:
    if not _targets_mcp_tool(handler):
        return
    if getattr(connection.app.state, "block_controller", False):
        raise NotAuthorizedException(_CONTROLLER_BLOCKED)
    getattr(connection.app.state, "order", []).append("controller")


def _route_guard(connection: ASGIConnection[Any, Any, Any, Any], handler: BaseRouteHandler) -> None:
    if not _targets_mcp_tool(handler):
        return
    if getattr(connection.app.state, "block_route", False):
        raise NotAuthorizedException(_ROUTE_BLOCKED)
    getattr(connection.app.state, "order", []).append("route")


@get("/x", opt={"mcp_tool": "x"}, sync_to_thread=False)
def _plain_tool() -> dict[str, str]:
    return {"ok": "yes"}


def test_app_guard_blocks_mcp_tool_call() -> None:
    app = Litestar(route_handlers=[_plain_tool], plugins=[LitestarMCP()], guards=[_app_guard])
    app.state.block_app = True

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert "app-guard blocked" in str(resp["result"])


def test_router_guard_blocks_mcp_tool_call() -> None:
    router = Router(path="/api", guards=[_router_guard], route_handlers=[_plain_tool])
    app = Litestar(route_handlers=[router], plugins=[LitestarMCP()])
    app.state.block_router = True

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert "router-guard blocked" in str(resp["result"])


def test_controller_guard_blocks_mcp_tool_call() -> None:
    class Notes(Controller):
        path = "/notes"
        guards = [_controller_guard]

        @get("/x", opt={"mcp_tool": "x"}, sync_to_thread=False)
        def tool(self) -> dict[str, str]:
            return {"ok": "yes"}

    app = Litestar(route_handlers=[Notes], plugins=[LitestarMCP()])
    app.state.block_controller = True

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert "controller-guard blocked" in str(resp["result"])


def test_route_guard_blocks_mcp_tool_call() -> None:
    @get("/x", guards=[_route_guard], opt={"mcp_tool": "x"}, sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    app.state.block_route = True

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert "route-guard blocked" in str(resp["result"])


def test_all_layers_compose_in_order() -> None:
    class Notes(Controller):
        path = "/notes"
        guards = [_controller_guard]

        @get("/x", guards=[_route_guard], opt={"mcp_tool": "x"}, sync_to_thread=False)
        def tool(self) -> dict[str, str]:
            return {"ok": "yes"}

    router = Router(path="/api", guards=[_router_guard], route_handlers=[Notes])
    app = Litestar(route_handlers=[router], plugins=[LitestarMCP()], guards=[_app_guard])
    app.state.order = []

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert app.state.order == ["app", "router", "controller", "route"]


def test_permission_denied_aborts_cleanly() -> None:
    denied = "nope"

    def denier(_connection: ASGIConnection[Any, Any, Any, Any], _handler: BaseRouteHandler) -> None:
        raise PermissionDeniedException(denied)

    @get("/x", guards=[denier], opt={"mcp_tool": "x"}, sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert "nope" in str(resp["result"])


def test_sync_and_async_guards_both_run() -> None:
    calls: list[str] = []

    async def async_guard(_c: ASGIConnection[Any, Any, Any, Any], _h: BaseRouteHandler) -> None:
        calls.append("async")

    def sync_guard(_c: ASGIConnection[Any, Any, Any, Any], _h: BaseRouteHandler) -> None:
        calls.append("sync")

    @get("/x", guards=[async_guard, sync_guard], opt={"mcp_tool": "x"}, sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert calls == ["async", "sync"]


def test_guard_runs_before_dependency_resolution() -> None:
    dep_err = "dep should not be invoked"
    guard_err = "guard-first"

    def broken_dep() -> str:
        raise RuntimeError(dep_err)

    def guard(_c: ASGIConnection[Any, Any, Any, Any], _h: BaseRouteHandler) -> None:
        raise NotAuthorizedException(guard_err)

    @get(
        "/x",
        guards=[guard],
        dependencies={"value": Provide(broken_dep, sync_to_thread=False)},
        opt={"mcp_tool": "x"},
        sync_to_thread=False,
    )
    def tool(value: str) -> dict[str, str]:
        return {"value": value}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    payload = str(resp["result"])
    assert "guard-first" in payload
    assert "dep should not be invoked" not in payload
