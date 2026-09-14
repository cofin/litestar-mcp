"""Tests for the in-process streaming ASGI transport."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx
import pytest
from litestar import Litestar, Request, get, post
from litestar.events import listener

from litestar_mcp import LitestarMCP, get_mcp_request_context
from litestar_mcp.mcp.bridge import run_stdio_streamable_http_bridge
from litestar_mcp.mcp.stdio import ASGIStreamingTransport, _app_lifespan, run_stdio_async
from tests.conftest import BridgeBytesSink, BridgeQueuedBytesSource


def _rpc_line(method: str, params: "dict[str, Any] | None" = None, *, msg_id: int = 1) -> bytes:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    return json.dumps(payload).encode() + b"\n"


@pytest.mark.anyio
async def test_stdio_startup_cancellation_releases_lifespan_and_event_tasks() -> None:
    startup_waiting = asyncio.Event()
    listener_started = asyncio.Event()
    listener_finished = asyncio.Event()
    resource_closed = asyncio.Event()
    lifecycle_tasks: list[asyncio.Task[Any]] = []

    @listener("startup-probe")
    async def event_listener() -> None:
        listener_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            with anyio.CancelScope(shield=True):
                await asyncio.sleep(0)
                listener_finished.set()

    @contextlib.asynccontextmanager
    async def resource(app: Litestar) -> AsyncIterator[None]:
        current = asyncio.current_task()
        assert current is not None
        lifecycle_tasks.append(current)
        try:
            yield
        finally:
            await asyncio.sleep(0)
            resource_closed.set()

    async def startup(app: Litestar) -> None:
        app.emit("startup-probe")
        await listener_started.wait()
        startup_waiting.set()
        await asyncio.Event().wait()

    app = Litestar(plugins=[LitestarMCP()], lifespan=[resource], listeners=[event_listener], on_startup=[startup])
    caller = asyncio.create_task(run_stdio_async(app, stdin=BridgeQueuedBytesSource(), stdout=BridgeBytesSink()))
    try:
        with anyio.fail_after(2):
            await startup_waiting.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        assert resource_closed.is_set(), "Startup cancellation left the entered lifespan resource open"
        assert listener_finished.is_set(), "Startup cancellation left the event listener running"
    finally:
        for task in lifecycle_tasks:
            if not task.done():
                task.cancel()
        with anyio.fail_after(2):
            await asyncio.gather(caller, *lifecycle_tasks, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("cancellation", ["asyncio", "anyio"])
async def test_stdio_body_cancellation_finishes_native_lifespan(cancellation: str) -> None:
    body_started = asyncio.Event()
    resource_closed = asyncio.Event()
    shutdown_finished = asyncio.Event()
    cancelled = asyncio.Event()
    scope = anyio.CancelScope()

    @contextlib.asynccontextmanager
    async def resource(app: Litestar) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await asyncio.sleep(0)
            resource_closed.set()

    async def shutdown() -> None:
        await asyncio.sleep(0)
        shutdown_finished.set()

    app = Litestar(lifespan=[resource], on_shutdown=[shutdown], logging_config=None)

    async def run() -> None:
        with scope:
            try:
                async with _app_lifespan(app):
                    body_started.set()
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    caller = asyncio.create_task(run())
    with anyio.fail_after(2):
        await body_started.wait()
        if cancellation == "asyncio":
            caller.cancel()
        else:
            scope.cancel()
        await asyncio.gather(caller, return_exceptions=True)

    assert cancelled.is_set()
    assert resource_closed.is_set()
    assert shutdown_finished.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("shutdown_failure", ["timeout", "exception"])
async def test_stdio_preserves_body_exception_when_shutdown_fails(
    shutdown_failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    original = ValueError("bridge failed")
    shutdown_started = asyncio.Event()
    resource_closed = asyncio.Event()

    async def bridge() -> None:
        raise original

    @contextlib.asynccontextmanager
    async def resource(app: Litestar) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await asyncio.sleep(0)
            resource_closed.set()

    async def shutdown() -> None:
        shutdown_started.set()
        if shutdown_failure == "exception":
            msg = "shutdown failed"
            raise RuntimeError(msg)
        await asyncio.Event().wait()

    app = Litestar(lifespan=[resource], on_shutdown=[shutdown], logging_config=None)
    with pytest.raises(ValueError) as caught, anyio.fail_after(2):
        async with _app_lifespan(app, shutdown_timeout=0.02):
            await bridge()

    assert caught.value is original
    assert resource_closed.is_set()
    assert shutdown_started.is_set()
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    if shutdown_failure == "timeout":
        assert [record.getMessage() for record in warnings] == ["Lifespan shutdown incomplete after 0.02 seconds"]
    else:
        assert [record.getMessage() for record in warnings] == ["Lifespan shutdown failed after a body error"]
        assert warnings[0].exc_info is not None
        assert str(warnings[0].exc_info[1]) == "shutdown failed"


@pytest.mark.anyio
async def test_stdio_preserves_body_cancellation_when_shutdown_times_out(caplog: pytest.LogCaptureFixture) -> None:
    original = asyncio.CancelledError("bridge cancelled")
    shutdown_cancelled = asyncio.Event()

    async def bridge() -> None:
        raise original

    async def shutdown() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            shutdown_cancelled.set()

    with pytest.raises(asyncio.CancelledError) as caught, anyio.fail_after(2):
        async with _app_lifespan(Litestar(on_shutdown=[shutdown], logging_config=None), shutdown_timeout=0.02):
            await bridge()

    assert caught.value is original
    assert shutdown_cancelled.is_set()
    assert "Lifespan shutdown incomplete" in caplog.text


@pytest.mark.anyio
async def test_stdio_shutdown_timeout_is_logged_and_releases_caller(caplog: pytest.LogCaptureFixture) -> None:
    shutdown_cancelled = asyncio.Event()

    async def shutdown() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            shutdown_cancelled.set()

    with anyio.fail_after(2):
        async with _app_lifespan(Litestar(on_shutdown=[shutdown], logging_config=None), shutdown_timeout=0.02):
            pass

    assert shutdown_cancelled.is_set()
    assert "Lifespan shutdown incomplete" in caplog.text


@pytest.mark.anyio
async def test_stdio_shutdown_failure_is_propagated() -> None:
    original = RuntimeError("shutdown failed")

    async def shutdown() -> None:
        raise original

    with pytest.raises(RuntimeError) as caught:
        async with _app_lifespan(Litestar(on_shutdown=[shutdown])):
            pass

    assert caught.value is original


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
async def test_transport_applies_backpressure_when_consumer_is_blocked() -> None:
    second_send_started = asyncio.Event()
    second_sent = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        second_send_started.set()
        await send({"type": "http.response.body", "body": b"second", "more_body": False})
        second_sent.set()

    async with (
        httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client,
        client.stream("POST", "http://mcp-stdio/mcp", content=b"{}") as response,
    ):
        chunks = response.aiter_bytes()
        with anyio.fail_after(2):
            await second_send_started.wait()
        assert not second_sent.is_set()
        assert await chunks.__anext__() == b"first"
        assert await chunks.__anext__() == b"second"
        with anyio.fail_after(2):
            await second_sent.wait()


@pytest.mark.anyio
async def test_transport_closes_unread_full_buffer_and_finalizes_app() -> None:
    second_send_started = asyncio.Event()
    finalized = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            second_send_started.set()
            await send({"type": "http.response.body", "body": b"second", "more_body": True})
        finally:
            await asyncio.sleep(0)
            finalized.set()

    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client:
        response = await client.send(client.build_request("POST", "http://mcp-stdio/mcp"), stream=True)
        with anyio.fail_after(2):
            await second_send_started.wait()
        close_task = asyncio.create_task(response.aclose())
        try:
            done, _ = await asyncio.wait({close_task}, timeout=0.5)
            assert done, "Closing an unread response blocked on the full body buffer"
            await close_task
            assert finalized.is_set()
            await response.aclose()
        finally:
            close_task.cancel()
            await asyncio.gather(close_task, return_exceptions=True)


@pytest.mark.anyio
async def test_transport_drains_buffer_before_raising_app_error() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"last", "more_body": True})
        msg = "failure after response start"
        raise RuntimeError(msg)

    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client:
        with pytest.raises(RuntimeError, match="failure after response start"):
            async with client.stream("POST", "http://mcp-stdio/mcp") as response:
                chunks = response.aiter_bytes()
                assert await chunks.__anext__() == b"last"
                await chunks.__anext__()


@pytest.mark.anyio
async def test_transport_drains_final_buffer_at_eof() -> None:
    finalized = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"last", "more_body": False})
        finalized.set()

    async with (
        httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client,
        client.stream("POST", "http://mcp-stdio/mcp") as response,
    ):
        with anyio.fail_after(2):
            await finalized.wait()
        assert await response.aread() == b"last"


@pytest.mark.anyio
async def test_transport_preserves_async_cleanup_when_close_is_cancelled() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    finalized = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            finalized.set()

    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client:
        response = await client.send(client.build_request("POST", "http://mcp-stdio/mcp"), stream=True)
        close_task = asyncio.create_task(response.aclose())
        with anyio.fail_after(2):
            await cleanup_started.wait()
            close_task.cancel()
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await close_task
        assert finalized.is_set()


@pytest.mark.anyio
async def test_transport_does_not_report_delivery_after_response_close() -> None:
    delivered = asyncio.Event()
    finalized = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await send({"type": "http.response.body", "body": b"too late", "more_body": True})
                delivered.set()
            finally:
                await asyncio.sleep(0)
                finalized.set()

    async with (
        httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client,
        client.stream("POST", "http://mcp-stdio/mcp"),
    ):
        pass

    assert finalized.is_set()
    assert not delivered.is_set()


@pytest.mark.anyio
async def test_transport_bounds_cancellation_resistant_cleanup(caplog: pytest.LogCaptureFixture) -> None:
    release_cleanup = asyncio.Event()
    finalized = asyncio.Event()
    producer: list[asyncio.Task[Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        current = asyncio.current_task()
        assert current is not None
        producer.append(current)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        try:
            await asyncio.Event().wait()
        finally:
            while not release_cleanup.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await release_cleanup.wait()
            finalized.set()

    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app, shutdown_timeout=0.02)) as client:
        response = await client.send(client.build_request("POST", "http://mcp-stdio/mcp"), stream=True)
        try:
            with anyio.fail_after(2):
                await response.aclose()
                await response.aclose()
            assert "ASGI response cleanup incomplete" in caplog.text
            assert not finalized.is_set()
        finally:
            release_cleanup.set()
            with anyio.fail_after(2):
                await asyncio.gather(*producer, return_exceptions=True)
        assert finalized.is_set()


@pytest.mark.parametrize("shutdown_timeout", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_transport_rejects_invalid_shutdown_timeout(shutdown_timeout: float) -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        pass

    with pytest.raises(ValueError, match="shutdown_timeout must be positive and finite"):
        ASGIStreamingTransport(app, shutdown_timeout=shutdown_timeout)


@pytest.mark.anyio
async def test_transport_uses_scheme_default_server_port() -> None:
    observed: list[tuple[str, int | None]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        observed.append(scope["server"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async with httpx.AsyncClient(transport=ASGIStreamingTransport(app)) as client:
        response = await client.get("https://example.test/path")

    assert response.status_code == 204
    assert observed == [("example.test", 443)]


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


@pytest.mark.anyio
async def test_stdio_body_exception_keeps_its_cause_chain() -> None:
    async def bridge() -> None:
        msg = "outer"
        try:
            int("inner")
        except ValueError as exc:
            raise RuntimeError(msg) from exc

    app = Litestar(logging_config=None)
    with pytest.raises(RuntimeError) as caught, anyio.fail_after(2):
        async with _app_lifespan(app, shutdown_timeout=0.5):
            await bridge()

    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.anyio
async def test_stdio_body_exception_without_cause_gains_no_lifespan_context(caplog: pytest.LogCaptureFixture) -> None:
    original = RuntimeError("body")

    async def bridge() -> None:
        raise original

    app = Litestar(logging_config=None)
    with pytest.raises(RuntimeError) as caught, anyio.fail_after(2):
        async with _app_lifespan(app, shutdown_timeout=0.5):
            await bridge()

    assert caught.value is original
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "Lifespan shutdown failed" not in caplog.text
