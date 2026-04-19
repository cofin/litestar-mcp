"""Red-phase tests for handler lifecycle hooks in the MCP executor (GH #41).

These pin the contract that ``execute_tool`` must run the same
``before_request`` / ``after_request`` / ``after_response`` pipeline as an
HTTP request, matching Litestar's resolution semantics (closest-wins).

Every test is expected to fail until Phase 3 rewrites the executor around
``handler.to_response`` + capture-send.
"""

import asyncio
import logging
from typing import Any

import pytest
from litestar import Controller, Litestar, Request, Router, post
from litestar.exceptions import NotAuthorizedException
from litestar.response import Response
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.executor import execute_tool
from tests.unit.conftest import get_handler_from_app

pytestmark = pytest.mark.unit


def _ensure_session(client: TestClient[Any]) -> str:
    sid = getattr(client, "_mcp_session", None)
    if sid is not None:
        return str(sid)
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid_val = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid_val},
    )
    client._mcp_session = sid_val  # type: ignore[attr-defined]
    return str(sid_val)


def _rpc(client: TestClient[Any], method: str, params: "dict[str, Any] | None" = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def _call_tool(client: TestClient[Any], name: str, args: "dict[str, Any] | None" = None) -> dict[str, Any]:
    return _rpc(client, "tools/call", {"name": name, "arguments": args or {}})


# ---------------------------------------------------------------------------
# before_request
# ---------------------------------------------------------------------------


def test_before_request_fires_for_mcp_tool_call() -> None:
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append("before")

    @post("/x", before_request=hook, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        seen.append("handler")
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert seen == ["before", "handler"]


def test_before_request_truthy_short_circuits_handler() -> None:
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> dict[str, str]:
        seen.append("short_circuit")
        return {"short_circuited": "yes"}

    no_fire = "should not fire"

    @post("/x", before_request=hook, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        seen.append("handler")
        raise RuntimeError(no_fire)

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert "short_circuited" in resp["result"]["content"][0]["text"]
    assert seen == ["short_circuit"]


@pytest.mark.parametrize("falsy_value", [None, "", 0, [], {}])
def test_before_request_falsy_falls_through_to_handler(falsy_value: Any) -> None:
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> Any:
        seen.append("before")
        return falsy_value

    @post("/x", before_request=hook, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        seen.append("handler")
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert seen == ["before", "handler"]


# ---------------------------------------------------------------------------
# after_response
# ---------------------------------------------------------------------------


def test_after_response_fires_on_success() -> None:
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append("after_response")

    @post("/x", after_response=hook, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        seen.append("handler")
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        _call_tool(client, "x")

    assert seen == ["handler", "after_response"]


def test_after_response_fires_on_handler_exception() -> None:
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append("after_response")

    boom = "boom"

    @post("/x", after_response=hook, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        seen.append("handler")
        raise RuntimeError(boom)

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert seen == ["handler", "after_response"]


def test_after_response_fires_on_guard_failure() -> None:
    seen: list[str] = []
    blocked = "blocked"

    def deny(_c: Any, _h: Any) -> None:
        seen.append("guard")
        raise NotAuthorizedException(blocked)

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append("after_response")

    @post("/x", after_response=hook, guards=[deny], mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        seen.append("handler")
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert "handler" not in seen
    assert seen[-1] == "after_response"


def test_after_response_failure_is_logged_and_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    blew_up = "after_response blew up"

    async def hook(_request: Request[Any, Any, Any]) -> None:
        raise RuntimeError(blew_up)

    @post("/x", after_response=hook, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    # ``logging_config=None`` keeps Litestar from replacing the root logger's
    # handlers — caplog's handler must survive to capture the executor's
    # exception log.
    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()], logging_config=None)
    caplog.set_level(logging.ERROR)
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    # Handler succeeded; after_response failure must not surface as an error.
    assert resp["result"]["isError"] is False
    matching = [rec for rec in caplog.records if rec.name == "litestar_mcp.executor" and rec.exc_info is not None]
    assert matching, "expected an exception log record from litestar_mcp.executor"


# ---------------------------------------------------------------------------
# after_request — response transformer, closest-wins
# ---------------------------------------------------------------------------


def test_after_request_mutates_tool_result_content() -> None:
    async def mutator(response: Response[Any]) -> Response[Any]:
        if isinstance(response.content, dict):
            response.content = {**response.content, "mutated_by": "after_request"}
        return response

    @post("/x", after_request=mutator, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert "after_request" in resp["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# Layer inheritance — closest-wins matches Litestar HTTP semantics
#
# Only router / controller / route layers fire through the tool dispatch.
# App-level ``before_request`` / ``after_response`` hooks are already
# invoked by Litestar's handler pipeline on the outer ``/mcp`` HTTP request,
# so the executor skips them to avoid double-firing. App-level behavior is
# covered separately below.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layer", ["router", "controller", "route"])
def test_before_request_resolves_from_ownership_layer(layer: str) -> None:
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append(f"before:{layer}")

    if layer == "route":

        @post("/x", before_request=hook, mcp_tool="x", sync_to_thread=False)
        def route_tool() -> dict[str, str]:
            return {"ok": "yes"}

        app = Litestar(route_handlers=[route_tool], plugins=[LitestarMCP()])

    elif layer == "controller":

        class Notes(Controller):
            path = "/notes"
            before_request = hook  # type: ignore[assignment]

            @post("/x", mcp_tool="x", sync_to_thread=False)
            def tool(self) -> dict[str, str]:
                return {"ok": "yes"}

        app = Litestar(route_handlers=[Notes], plugins=[LitestarMCP()])

    else:  # layer == "router"

        @post("/x", mcp_tool="x", sync_to_thread=False)
        def rt_tool() -> dict[str, str]:
            return {"ok": "yes"}

        router = Router(path="/api", before_request=hook, route_handlers=[rt_tool])
        app = Litestar(route_handlers=[router], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert seen == [f"before:{layer}"]


@pytest.mark.parametrize("layer", ["router", "controller", "route"])
def test_after_response_resolves_from_ownership_layer(layer: str) -> None:
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append(f"after:{layer}")

    if layer == "route":

        @post("/x", after_response=hook, mcp_tool="x", sync_to_thread=False)
        def route_tool() -> dict[str, str]:
            return {"ok": "yes"}

        app = Litestar(route_handlers=[route_tool], plugins=[LitestarMCP()])

    elif layer == "controller":

        class Notes(Controller):
            path = "/notes"
            after_response = hook  # type: ignore[assignment]

            @post("/x", mcp_tool="x", sync_to_thread=False)
            def tool(self) -> dict[str, str]:
                return {"ok": "yes"}

        app = Litestar(route_handlers=[Notes], plugins=[LitestarMCP()])

    else:  # layer == "router"

        @post("/x", mcp_tool="x", sync_to_thread=False)
        def rt_tool() -> dict[str, str]:
            return {"ok": "yes"}

        router = Router(path="/api", after_response=hook, route_handlers=[rt_tool])
        app = Litestar(route_handlers=[router], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        _call_tool(client, "x")

    assert seen == [f"after:{layer}"]


def test_app_level_before_request_fires_via_outer_mcp_request_not_tool_dispatch() -> None:
    """App-level ``before_request`` applies via the outer ``/mcp`` HTTP request.

    The executor deliberately skips the hook during tool dispatch to avoid
    double-firing. Covers the semantic that HTTP parity is preserved at the
    ``/mcp`` envelope level.
    """
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append("app")

    @post("/x", mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()], before_request=hook)
    with TestClient(app=app) as client:
        _call_tool(client, "x")

    # Hook fired at least once per /mcp HTTP request (init + tools/call +
    # optionally notifications). No extra firings from the tool dispatch.
    assert seen.count("app") >= 1
    # Count must equal the number of /mcp HTTP POSTs, not 2x that.
    mcp_request_count = 3  # initialize + notifications/initialized + tools/call
    assert len(seen) <= mcp_request_count, f"expected ≤{mcp_request_count} firings, got {len(seen)}"


def test_app_level_after_response_fires_via_outer_mcp_request_not_tool_dispatch() -> None:
    """Same principle as ``before_request``: app-level hooks run via ``/mcp``, not via dispatch."""
    seen: list[str] = []

    async def hook(_request: Request[Any, Any, Any]) -> None:
        seen.append("app")

    @post("/x", mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()], after_response=hook)
    with TestClient(app=app) as client:
        _call_tool(client, "x")

    assert seen.count("app") >= 1
    mcp_request_count = 3
    assert len(seen) <= mcp_request_count, f"expected ≤{mcp_request_count} firings, got {len(seen)}"


# ---------------------------------------------------------------------------
# Short-circuit still runs after_response
# ---------------------------------------------------------------------------


def test_short_circuit_skips_handler_but_runs_after_response() -> None:
    seen: list[str] = []

    async def before(_request: Request[Any, Any, Any]) -> dict[str, str]:
        seen.append("before")
        return {"short": "yes"}

    async def after(_request: Request[Any, Any, Any]) -> None:
        seen.append("after")

    must_not_run = "must not run"

    @post("/x", before_request=before, after_response=after, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        seen.append("handler")
        raise RuntimeError(must_not_run)

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert seen == ["before", "after"]


# ---------------------------------------------------------------------------
# Execution order
# ---------------------------------------------------------------------------


def test_hook_execution_order_success_path() -> None:
    events: list[str] = []

    async def before(_r: Request[Any, Any, Any]) -> None:
        events.append("before_request")

    async def after_req(resp: Response[Any]) -> Response[Any]:
        events.append("after_request")
        return resp

    async def after_resp(_r: Request[Any, Any, Any]) -> None:
        events.append("after_response")

    def guard(_c: Any, _h: Any) -> None:
        events.append("guard")

    @post(
        "/x",
        before_request=before,
        after_request=after_req,
        after_response=after_resp,
        guards=[guard],
        mcp_tool="x",
        sync_to_thread=False,
    )
    def tool() -> dict[str, str]:
        events.append("handler")
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        _call_tool(client, "x")

    assert events == ["guard", "before_request", "handler", "after_request", "after_response"]


def test_hook_execution_order_exception_path() -> None:
    events: list[str] = []

    async def before(_r: Request[Any, Any, Any]) -> None:
        events.append("before_request")

    async def after_resp(_r: Request[Any, Any, Any]) -> None:
        events.append("after_response")

    boom = "boom"

    @post(
        "/x",
        before_request=before,
        after_response=after_resp,
        mcp_tool="x",
        sync_to_thread=False,
    )
    def tool() -> dict[str, str]:
        events.append("handler")
        raise RuntimeError(boom)

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert events == ["before_request", "handler", "after_response"]


# ---------------------------------------------------------------------------
# stdio mode
# ---------------------------------------------------------------------------


def test_hooks_run_in_stdio_mode() -> None:
    events: list[str] = []

    async def before(_r: Request[Any, Any, Any]) -> None:
        events.append("before")

    async def after(_r: Request[Any, Any, Any]) -> None:
        events.append("after")

    @post("/x", before_request=before, after_response=after, mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        events.append("handler")
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    handler = get_handler_from_app(app, "/x", method="POST")

    result = asyncio.run(execute_tool(handler, app, {}))

    assert result == {"ok": "yes"}
    assert events == ["before", "handler", "after"]
