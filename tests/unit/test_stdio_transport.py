"""Tests for the in-process streaming ASGI transport."""

import asyncio
import json
from typing import Any

import anyio
import httpx
import pytest
from litestar import Litestar, Request, get, post

from litestar_mcp import LitestarMCP, get_mcp_request_context
from litestar_mcp.mcp.bridge import run_stdio_streamable_http_bridge
from litestar_mcp.mcp.stdio import ASGIStreamingTransport
from tests.conftest import BridgeBytesSink, BridgeQueuedBytesSource


def _rpc_line(method: str, params: "dict[str, Any] | None" = None, *, msg_id: int = 1) -> bytes:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    return json.dumps(payload).encode() + b"\n"


@pytest.mark.anyio
async def test_transport_streams_chunks_before_app_completes() -> None:
    release = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await release.wait()
        await send({"type": "http.response.body", "body": b"second", "more_body": False})

    async with (
        httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client,
        client.stream("POST", "http://mcp-stdio/mcp", content=b"{}") as response,
    ):
        chunks = response.aiter_bytes()
        with anyio.fail_after(2):
            assert await chunks.__anext__() == b"first"
        assert not release.is_set()
        release.set()
        with anyio.fail_after(2):
            assert await chunks.__anext__() == b"second"
        assert response.status_code == 200


@pytest.mark.anyio
async def test_transport_close_cancels_app_and_signals_disconnect() -> None:
    observed: list[str] = []
    started = asyncio.Event()
    disconnect_seen = asyncio.Event()
    background: set[asyncio.Task[None]] = set()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        while True:
            request_message = await receive()
            if not request_message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"tick", "more_body": True})

        async def watch_disconnect() -> None:
            message = await receive()
            observed.append(message["type"])
            disconnect_seen.set()

        background.add(asyncio.create_task(watch_disconnect()))
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            observed.append("cancelled")
            raise

    async with (
        httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client,
        client.stream("POST", "http://mcp-stdio/mcp", content=b"{}") as response,
    ):
        with anyio.fail_after(2):
            assert await response.aiter_bytes().__anext__() == b"tick"
            await started.wait()

    with anyio.fail_after(2):
        await disconnect_seen.wait()
    assert "cancelled" in observed
    assert "http.disconnect" in observed


@pytest.mark.anyio
async def test_transport_cancelled_before_response_start_aborts_app() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client:
        scope = anyio.CancelScope()

        async def caller() -> None:
            with scope:
                await client.post("http://mcp-stdio/mcp", content=b"{}")

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(caller)
            with anyio.fail_after(2):
                await started.wait()
            scope.cancel()

    with anyio.fail_after(2):
        await cancelled.wait()


@pytest.mark.anyio
async def test_transport_honours_read_timeout_for_body_chunks() -> None:
    cancelled = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x", "more_body": True})
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    timeout = httpx.Timeout(5.0, read=0.2)
    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app), timeout=timeout) as client:
        with pytest.raises(httpx.ReadTimeout), anyio.fail_after(3):
            await client.post("http://mcp-stdio/mcp", content=b"{}")

    assert cancelled.is_set()


@pytest.mark.anyio
async def test_transport_honours_read_timeout_for_response_start() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await asyncio.sleep(30)

    timeout = httpx.Timeout(5.0, read=0.2)
    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app), timeout=timeout) as client:
        with pytest.raises(httpx.ReadTimeout), anyio.fail_after(3):
            await client.post("http://mcp-stdio/mcp", content=b"{}")


@pytest.mark.anyio
async def test_transport_raises_app_exception_before_response_start() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client:
        with pytest.raises(RuntimeError, match="boom"):
            await client.post("http://mcp-stdio/mcp", content=b"{}")


@pytest.mark.anyio
async def test_transport_round_trips_a_litestar_app() -> None:
    @post("/echo", sync_to_thread=False)
    def echo(request: "Request[Any, Any, Any]", data: "dict[str, Any]") -> "dict[str, Any]":
        client = request.scope["client"]
        return {"data": data, "host": request.url.hostname, "client": list(client) if client else None}

    app = Litestar(route_handlers=[echo])
    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client:
        response = await client.post("http://mcp-stdio/echo", json={"x": 1})

    assert response.status_code == 201
    assert response.json() == {"data": {"x": 1}, "host": "mcp-stdio", "client": ["mcp-stdio", 0]}


@pytest.mark.anyio
async def test_bridge_runs_over_injected_transport_with_client_info() -> None:
    @get("/whoami", mcp_tool="whoami", sync_to_thread=False)
    def whoami() -> "dict[str, Any]":
        return {"client_info": get_mcp_request_context().client_info}

    app = Litestar(route_handlers=[whoami], plugins=[LitestarMCP()])
    stdin = BridgeQueuedBytesSource(
        _rpc_line("server/discover", msg_id=1),
        _rpc_line("tools/call", {"name": "whoami", "arguments": {}}, msg_id=2),
    )
    stdout = BridgeBytesSink()

    exit_code = await run_stdio_streamable_http_bridge(
        "http://mcp-stdio/mcp",
        transport=ASGIStreamingTransport(app),
        client_info={"name": "unit-test", "version": "0"},
        stdin=stdin,
        stdout=stdout,
    )

    assert exit_code == 0
    messages = {message["id"]: message for message in (json.loads(line) for line in stdout.buffer.splitlines())}
    assert "result" in messages[1]
    payload = json.loads(messages[2]["result"]["content"][0]["text"])
    assert payload == {"client_info": {"name": "unit-test", "version": "0"}}
