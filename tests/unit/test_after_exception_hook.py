"""Red-phase tests for ``after_exception`` observability hooks (#41).

``Litestar(after_exception=...)`` is an app-level list of observers with
signature ``(exc, scope) -> None``. Litestar HTTP fires them **before**
``exception_handlers`` dispatch; the MCP executor must match.

Verified in Litestar HTTP during spec design — see spec Phase 1.7 notes.
"""

import logging
from typing import Any

import pytest
from litestar import Litestar, Request, post
from litestar.response import Response
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP

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


def _call_tool(client: TestClient[Any], name: str) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    sid = _ensure_session(client)
    headers = {"Mcp-Session-Id": sid} if sid else {}
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


class _ObservedError(Exception):
    """Domain exception used for observer tests."""


def test_after_exception_fires_on_uncaught_exception() -> None:
    seen: list[str] = []

    async def observer(exc: Exception, scope: Any) -> None:
        seen.append(type(exc).__name__)

    boom = "boom"

    @post("/x", mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        raise _ObservedError(boom)

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()], after_exception=[observer])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is True
    assert seen == ["_ObservedError"]


def test_after_exception_fires_even_when_handler_recovers() -> None:
    """Parity with Litestar HTTP — observer fires before exception_handlers dispatch."""
    seen: list[str] = []

    async def observer(exc: Exception, _scope: Any) -> None:
        seen.append(f"observed:{type(exc).__name__}")

    def recovery(_request: Request[Any, Any, Any], exc: _ObservedError) -> Response[Any]:
        seen.append(f"handler:{type(exc).__name__}")
        return Response(content={"recovered": True}, status_code=200)

    soft = "soft"

    @post(
        "/x",
        exception_handlers={_ObservedError: recovery},
        mcp_tool="x",
        sync_to_thread=False,
    )
    def tool() -> dict[str, str]:
        raise _ObservedError(soft)

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()], after_exception=[observer])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    assert resp["result"]["isError"] is False
    assert seen == ["observed:_ObservedError", "handler:_ObservedError"]


def test_after_exception_failure_is_logged_and_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    """A broken observer must not mask the original exception or crash the executor."""

    exploded = "observer exploded"
    original = "original"

    async def broken(_exc: Exception, _scope: Any) -> None:
        raise RuntimeError(exploded)

    @post("/x", mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        raise _ObservedError(original)

    app = Litestar(
        route_handlers=[tool],
        plugins=[LitestarMCP()],
        after_exception=[broken],
        logging_config=None,
    )
    caplog.set_level(logging.ERROR)
    with TestClient(app=app) as client:
        resp = _call_tool(client, "x")

    # The ORIGINAL exception must still bubble to the blanket catch.
    assert resp["result"]["isError"] is True
    assert "original" in resp["result"]["content"][0]["text"]
    matching = [rec for rec in caplog.records if rec.name == "litestar_mcp.executor" and rec.exc_info is not None]
    assert matching, "expected an exception log record from litestar_mcp.executor"
