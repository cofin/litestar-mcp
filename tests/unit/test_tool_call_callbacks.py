"""Tests for MCP tool-call observability callbacks."""

import logging
import time
from typing import Any

from litestar import Litestar, Request, get, post
from litestar.exceptions import NotAuthorizedException
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, mcp_tool
from litestar_mcp.executor import MCPToolErrorResult


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


def _call_tool(client: TestClient[Any], name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})


def test_tool_call_callbacks_fire_once_with_result_and_synthetic_request() -> None:
    events: list[tuple[str, str, dict[str, Any], Any, Exception | None, float | None]] = []

    async def before_tool_call(
        tool_name: str,
        arguments: dict[str, Any],
        request: Request[Any, Any, Any],
    ) -> None:
        events.append(("before", tool_name, dict(arguments), request.scope.get("route_handler"), None, None))
        arguments["count"] = 999

    async def after_tool_call(
        tool_name: str,
        arguments: dict[str, Any],
        request: Request[Any, Any, Any],
        *,
        result: Any,
        exception: Exception | None,
        duration: float,
    ) -> None:
        events.append(("after", tool_name, dict(arguments), result, exception, duration))
        assert request.scope.get("route_handler") is not None

    @post("/x", mcp_tool="x", sync_to_thread=False)
    def tool(count: int) -> dict[str, int]:
        events.append(("handler", "x", {"count": count}, None, None, None))
        return {"count": count}

    app = Litestar(
        route_handlers=[tool],
        plugins=[LitestarMCP(MCPConfig(before_tool_call=before_tool_call, after_tool_call=after_tool_call))],
    )
    with TestClient(app=app) as client:
        response = _call_tool(client, "x", {"count": 3})

    assert response["result"]["isError"] is False
    assert events[0][:3] == ("before", "x", {"count": 3})
    assert events[1][:3] == ("handler", "x", {"count": 3})
    assert events[2][0:4] == ("after", "x", {"count": 3}, {"count": 3})
    assert events[2][4] is None
    assert isinstance(events[2][5], float)
    assert events[2][5] >= 0


def test_after_tool_call_receives_handler_exception() -> None:
    seen: list[Exception | None] = []

    async def after_tool_call(
        _tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
        *,
        result: Any,
        exception: Exception | None,
        duration: float,
    ) -> None:
        assert result is None
        assert duration >= 0
        seen.append(exception)

    boom_message = "boom"

    @post("/boom", mcp_tool="boom", sync_to_thread=False)
    def boom() -> dict[str, str]:
        raise RuntimeError(boom_message)

    app = Litestar(route_handlers=[boom], plugins=[LitestarMCP(MCPConfig(after_tool_call=after_tool_call))])
    with TestClient(app=app) as client:
        response = _call_tool(client, "boom")

    assert response["result"]["isError"] is True
    assert len(seen) == 1
    assert isinstance(seen[0], RuntimeError)


def test_after_tool_call_receives_error_result_for_guard_failure() -> None:
    seen: list[Exception | None] = []
    blocked_message = "blocked"

    def deny(_connection: Any, _handler: Any) -> None:
        raise NotAuthorizedException(blocked_message)

    async def after_tool_call(
        _tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
        *,
        result: Any,
        exception: Exception | None,
        duration: float,
    ) -> None:
        assert result is None
        assert duration >= 0
        seen.append(exception)

    @post("/guarded", guards=[deny], mcp_tool="guarded", sync_to_thread=False)
    def guarded() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[guarded], plugins=[LitestarMCP(MCPConfig(after_tool_call=after_tool_call))])
    with TestClient(app=app) as client:
        response = _call_tool(client, "guarded")

    assert response["result"]["isError"] is True
    assert len(seen) == 1
    assert isinstance(seen[0], (MCPToolErrorResult, NotAuthorizedException))


def test_resource_read_does_not_fire_tool_call_callbacks() -> None:
    seen: list[str] = []

    async def before_tool_call(
        tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
    ) -> None:
        seen.append(tool_name)

    async def after_tool_call(
        tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
        *,
        result: Any,
        exception: Exception | None,
        duration: float,
    ) -> None:
        seen.append(tool_name)

    @get("/config", mcp_resource="config", sync_to_thread=False)
    def config() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(
        route_handlers=[config],
        plugins=[LitestarMCP(MCPConfig(before_tool_call=before_tool_call, after_tool_call=after_tool_call))],
    )
    with TestClient(app=app) as client:
        response = _rpc(client, "resources/read", {"uri": "litestar://config"})

    assert "result" in response
    assert seen == []


def test_tool_call_callback_failures_are_logged_and_swallowed(caplog: Any) -> None:
    before_message = "before failed"
    after_message = "after failed"

    async def before_tool_call(
        _tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
    ) -> None:
        raise RuntimeError(before_message)

    async def after_tool_call(
        _tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
        *,
        result: Any,
        exception: Exception | None,
        duration: float,
    ) -> None:
        raise RuntimeError(after_message)

    @post("/x", mcp_tool="x", sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(
        route_handlers=[tool],
        plugins=[LitestarMCP(MCPConfig(before_tool_call=before_tool_call, after_tool_call=after_tool_call))],
        logging_config=None,
    )
    caplog.set_level(logging.ERROR)
    with TestClient(app=app) as client:
        response = _call_tool(client, "x")

    assert response["result"]["isError"] is False
    records = [record for record in caplog.records if record.name == "litestar_mcp.executor"]
    assert len(records) == 2
    assert all(record.exc_info is not None for record in records)


def test_task_tool_calls_run_callbacks() -> None:
    seen: list[str] = []

    async def before_tool_call(
        tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
    ) -> None:
        seen.append(f"before:{tool_name}")

    async def after_tool_call(
        tool_name: str,
        _arguments: dict[str, Any],
        _request: Request[Any, Any, Any],
        *,
        result: Any,
        exception: Exception | None,
        duration: float,
    ) -> None:
        seen.append(f"after:{tool_name}:{exception is None}:{duration >= 0}:{result is not None}")

    @get("/tasked", sync_to_thread=False)
    @mcp_tool(name="tasked", task_support="optional")
    async def tasked(delay: float = 0.01) -> dict[str, bool]:
        import asyncio

        await asyncio.sleep(delay)
        return {"ok": True}

    app = Litestar(
        route_handlers=[tasked],
        plugins=[
            LitestarMCP(MCPConfig(tasks=True, before_tool_call=before_tool_call, after_tool_call=after_tool_call))
        ],
    )
    with TestClient(app=app) as client:
        response = _rpc(
            client,
            "tools/call",
            {"name": "tasked", "arguments": {"delay": 0.01}, "task": {"ttl": 1000}},
        )
        task_id = response["result"]["task"]["taskId"]
        time.sleep(0.05)
        task_result = _rpc(client, "tasks/result", {"taskId": task_id})

    assert task_result["result"]["isError"] is False
    assert seen == ["before:tasked", "after:tasked:True:True:True"]
