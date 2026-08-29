"""In-process stdio transport for Litestar MCP applications."""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, TypeVar, cast

import anyio
import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Mapping

    from litestar.types import ASGIApp

__all__ = ("ASGIStreamingTransport",)

_DEFAULT_CLIENT = ("mcp-stdio", 0)
_T = TypeVar("_T")


class _ASGIResponseState:
    __slots__ = ("body", "disconnected", "headers", "started", "status_code")

    def __init__(self) -> None:
        self.status_code: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.body: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.started = asyncio.Event()
        self.disconnected = asyncio.Event()


async def _wait(awaitable: "Awaitable[_T]", read_timeout: "float | None", request: "httpx.Request") -> "_T":
    if read_timeout is None:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, read_timeout)
    except asyncio.TimeoutError as exc:
        msg = "In-process ASGI response timed out"
        raise httpx.ReadTimeout(msg, request=request) from exc


class _ASGIResponseStream(httpx.AsyncByteStream):
    def __init__(
        self,
        state: "_ASGIResponseState",
        task: "asyncio.Task[None]",
        *,
        read_timeout: "float | None",
        request: "httpx.Request",
    ) -> None:
        self._state = state
        self._task = task
        self._read_timeout = read_timeout
        self._request = request

    async def __aiter__(self) -> "AsyncIterator[bytes]":
        while True:
            chunk = await _wait(self._state.body.get(), self._read_timeout, self._request)
            if chunk is None:
                break
            yield chunk
        await self._task

    async def aclose(self) -> None:
        await _shutdown_app_task(self._state, self._task)


async def _shutdown_app_task(state: "_ASGIResponseState", task: "asyncio.Task[None]") -> None:
    state.disconnected.set()
    if not task.done():
        task.cancel()
    with anyio.CancelScope(shield=True), contextlib.suppress(asyncio.CancelledError):
        await task


def _build_scope(request: "httpx.Request", client: "tuple[str, int]", root_path: "str") -> "dict[str, Any]":
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": request.method,
        "headers": [(key.lower(), value) for key, value in request.headers.raw],
        "scheme": request.url.scheme,
        "path": request.url.path,
        "raw_path": request.url.raw_path.split(b"?")[0],
        "query_string": request.url.query,
        "server": (request.url.host, request.url.port or 80),
        "client": client,
        "root_path": root_path,
        "extensions": {},
    }


class ASGIStreamingTransport(httpx.AsyncBaseTransport):
    """Run an ASGI application in-process and stream its response body to httpx.

    ``httpx.ASGITransport`` buffers every ``http.response.body`` chunk until
    the application returns, so Server-Sent Events never reach the client
    while the stream is open. This transport returns the response as soon as
    ``http.response.start`` arrives, hands each body chunk to the response
    stream as it is sent, and cancels the application task when the response
    is closed, or when the caller is cancelled before ``http.response.start``
    arrives, so an aborted request observes ``http.disconnect`` and task
    cancellation. The httpx ``read`` timeout bounds the wait for
    ``http.response.start`` and for each body chunk (``httpx.ReadTimeout``).

    Args:
        app: The ASGI application to call for every request.
        client: The ``scope["client"]`` tuple presented to the application.
        root_path: The ``scope["root_path"]`` presented to the application.
    """

    def __init__(
        self,
        app: "ASGIApp",
        *,
        client: "tuple[str, int]" = _DEFAULT_CLIENT,
        root_path: "str" = "",
    ) -> None:
        self._app = app
        self._client = client
        self._root_path = root_path

    async def handle_async_request(self, request: "httpx.Request") -> "httpx.Response":
        """Dispatch ``request`` to the application and return a streaming response."""
        request_stream = cast("httpx.AsyncByteStream", request.stream)
        scope = _build_scope(request, self._client, self._root_path)
        state = _ASGIResponseState()
        request_chunks = request_stream.__aiter__()
        request_complete = False

        async def receive() -> "dict[str, Any]":
            nonlocal request_complete
            if request_complete:
                await state.disconnected.wait()
                return {"type": "http.disconnect"}
            try:
                body = await request_chunks.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message: "Mapping[str, Any]") -> None:
            message_type = message["type"]
            if message_type == "http.response.start":
                state.status_code = int(message["status"])
                state.headers = [(bytes(key), bytes(value)) for key, value in message.get("headers", [])]
                state.started.set()
            elif message_type == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await state.body.put(body)
                if not message.get("more_body", False):
                    await state.body.put(None)

        async def run_app() -> None:
            try:
                await self._app(cast("Any", scope), cast("Any", receive), cast("Any", send))
            finally:
                state.started.set()
                state.body.put_nowait(None)

        read_timeout = cast("float | None", request.extensions.get("timeout", {}).get("read"))
        task = asyncio.create_task(run_app())
        try:
            await _wait(state.started.wait(), read_timeout, request)
        except BaseException:
            await _shutdown_app_task(state, task)
            raise
        if state.status_code is None:
            await task
            msg = "ASGI application completed without sending http.response.start"
            raise RuntimeError(msg)
        return httpx.Response(
            state.status_code,
            headers=state.headers,
            stream=_ASGIResponseStream(state, task, read_timeout=read_timeout, request=request),
            request=request,
        )
