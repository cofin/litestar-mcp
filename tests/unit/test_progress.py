"""Progress notifications flow on the response stream of the request that supplied the token."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import anyio
import pytest
from anyio.lowlevel import checkpoint
from litestar import Litestar, get
from litestar.di import NamedDependency, Provide
from litestar.testing import AsyncTestClient

from litestar_mcp import LitestarMCP, MCPConfig, get_mcp_request_context
from litestar_mcp.core.sse import SubscriptionManager

pytestmark = pytest.mark.anyio


@get("/slow", mcp_tool="slow")
async def slow() -> dict[str, Any]:
    context = get_mcp_request_context()
    await context.report_progress(1, total=2, message="half")
    return {"ok": True}


def _events(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


async def test_progress_token_switches_the_response_to_a_stream() -> None:
    app = Litestar(route_handlers=[slow], plugins=[LitestarMCP()])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "slow", "arguments": {}, "_meta": {"progressToken": "tok-1"}},
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert events[0]["method"] == "notifications/progress"
    assert events[0]["params"] == {"progressToken": "tok-1", "progress": 1, "total": 2, "message": "half"}
    assert events[-1]["id"] == 7
    assert events[-1]["result"]["content"][0]["text"] == '{"ok":true}'


async def test_without_progress_token_the_response_stays_json() -> None:
    @get("/plain", mcp_tool="plain", sync_to_thread=False)
    def plain() -> dict[str, Any]:
        return {"ok": True}

    app = Litestar(route_handlers=[plain], plugins=[LitestarMCP()])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "plain", "arguments": {}}},
        )

    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["result"]["content"][0]["text"] == '{"ok":true}'


async def test_progress_is_never_fanned_out_to_listen_streams() -> None:
    manager = SubscriptionManager()
    _stream_id, stream = await manager.open("listen", {"toolsListChanged": True})

    await manager.publish("notifications/progress", {"progressToken": "tok-2", "progress": 1})
    await manager.publish("notifications/tools/list_changed", {})
    acknowledgement = await stream.__anext__()
    delivered = await stream.__anext__()
    await stream.aclose()

    assert acknowledgement["method"] == "notifications/subscriptions/acknowledged"
    assert delivered["method"] == "notifications/tools/list_changed"


def _progress_scope() -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"mcp-protocol-version", b"2026-07-28"),
            (b"mcp-method", b"tools/call"),
            (b"mcp-name", b"progress"),
        ],
    }


def _progress_body() -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "progress",
                "arguments": {},
                "_meta": {
                    "progressToken": "token",
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        }
    ).encode()


class _ProgressExchange:
    def __init__(self) -> None:
        self.first_sent = asyncio.Event()
        self.release_reader = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.chunks: list[bytes] = []
        self._requested = False

    async def receive(self) -> dict[str, Any]:
        if not self._requested:
            self._requested = True
            return {"type": "http.request", "body": _progress_body()}
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict[str, Any]) -> None:
        if message["type"] != "http.response.body":
            return
        self.chunks.append(message.get("body", b""))
        if message.get("body") and not self.first_sent.is_set():
            self.first_sent.set()
            await self.release_reader.wait()
        if not message.get("more_body", False):
            self.disconnected.set()


@pytest.mark.parametrize("capacity", [1, 3])
async def test_progress_waits_for_buffer_space_and_preserves_every_report(capacity: int) -> None:
    exchange = _ProgressExchange()
    attempted = asyncio.Event()
    tool_complete = asyncio.Event()
    reported: list[int] = []
    total = capacity + 5

    @get("/work", mcp_tool="progress")
    async def progress() -> dict[str, int]:
        context = get_mcp_request_context()
        await context.report_progress(0, total=total)
        reported.append(0)
        await exchange.first_sent.wait()
        for index in range(1, total):
            if index == capacity + 1:
                attempted.set()
            await context.report_progress(index, total=total)
            reported.append(index)
        tool_complete.set()
        return {"reports": total}

    app = Litestar(route_handlers=[progress], plugins=[LitestarMCP(MCPConfig(stream_queue_capacity=capacity))])
    task = asyncio.create_task(
        app(cast("Any", _progress_scope()), cast("Any", exchange.receive), cast("Any", exchange.send))
    )
    with anyio.fail_after(2):
        try:
            await attempted.wait()
            await checkpoint()
            assert reported == list(range(capacity + 1))
            assert not tool_complete.is_set()
        finally:
            exchange.release_reader.set()
            await task

    events = _events(b"".join(exchange.chunks).decode())
    assert [event["params"]["progress"] for event in events[:-1]] == list(range(total))
    assert all(event["params"]["progressToken"] == "token" for event in events[:-1])
    assert events[-1]["id"] == 7
    assert json.loads(events[-1]["result"]["content"][0]["text"]) == {"reports": total}
    assert tool_complete.is_set()
    assert events[-1]["result"]["resultType"] == "complete"
    assert "io.modelcontextprotocol/serverInfo" in events[-1]["result"]["_meta"]


@pytest.mark.parametrize("retry_report", [False, True])
async def test_full_progress_buffer_disconnect_awaits_tool_and_dependency_cleanup(retry_report: bool) -> None:
    exchange = _ProgressExchange()
    attempted = asyncio.Event()
    tool_closed, dependency_closed, reported_after_close = asyncio.Event(), asyncio.Event(), asyncio.Event()
    producers: list[asyncio.Task[Any]] = []

    async def lease() -> AsyncGenerator[str, None]:
        try:
            yield "active"
        finally:
            await checkpoint()
            await checkpoint()
            dependency_closed.set()

    @get("/work", mcp_tool="progress", dependencies={"resource": Provide(lease)})
    async def progress(resource: NamedDependency[str]) -> None:
        assert resource == "active"
        current = asyncio.current_task()
        assert current is not None
        producers.append(current)
        context = get_mcp_request_context()
        try:
            await context.report_progress(0)
            await exchange.first_sent.wait()
            await context.report_progress(1)
            attempted.set()
            await context.report_progress(2)
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if retry_report:
                await context.report_progress(99)
                reported_after_close.set()
            raise
        finally:
            await checkpoint()
            await checkpoint()
            tool_closed.set()

    app = Litestar(route_handlers=[progress], plugins=[LitestarMCP(MCPConfig(stream_queue_capacity=1))])
    task = asyncio.create_task(
        app(cast("Any", _progress_scope()), cast("Any", exchange.receive), cast("Any", exchange.send))
    )
    try:
        with anyio.fail_after(2):
            await attempted.wait()
            exchange.disconnected.set()
            await task
        assert tool_closed.is_set() and dependency_closed.is_set()
        assert not reported_after_close.is_set()
        assert all(producer.done() for producer in producers)
    finally:
        task.cancel()
        for producer in producers:
            producer.cancel()
        await asyncio.gather(task, *producers, return_exceptions=True)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_progress_cleanup_timeout_must_be_positive_and_finite(timeout: float) -> None:
    with pytest.raises(ValueError, match="stream_cleanup_timeout must be positive and finite"):
        MCPConfig(stream_cleanup_timeout=timeout)


async def test_progress_token_without_reports_still_emits_one_final_result() -> None:
    @get("/work", mcp_tool="progress")
    async def progress() -> dict[str, bool]:
        return {"ok": True}

    app = Litestar(route_handlers=[progress], plugins=[LitestarMCP()])
    async with AsyncTestClient(app=app) as client:
        response = await client.post("/mcp", json=json.loads(_progress_body()))

    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert len(events) == 1
    assert events[0]["id"] == 7
    assert json.loads(events[0]["result"]["content"][0]["text"]) == {"ok": True}
