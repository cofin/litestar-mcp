"""In-process stdio transport for Litestar MCP applications."""

import asyncio
import contextlib
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

import anyio
import httpx

from litestar_mcp.__metadata__ import __version__
from litestar_mcp.mcp.bridge import (
    BEARER_TOKEN_PREFIX,
    DEFAULT_AUTH_HEADER_NAME,
    DEFAULT_MAX_STDIN_MESSAGE_SIZE,
    TokenProvider,
    run_stdio_streamable_http_bridge,
)
from litestar_mcp.mcp.routes import MCP_OWNER_ID_SCOPE_KEY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

    from anyio.abc import ByteReceiveStream, ByteSendStream
    from litestar import Litestar
    from litestar.types import ASGIApp

__all__ = ("ASGIStreamingTransport", "MCPStdioContext", "run_stdio", "run_stdio_async")

_DEFAULT_CLIENT = ("mcp-stdio", 0)
_T = TypeVar("_T")
_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MCPStdioContext:
    """Runtime identity context for standalone MCP stdio transports."""

    client_id: "str" = "stdio"
    owner_id: "str | None" = None
    user: "Any" = None
    auth: "Any" = None
    session: "Mapping[str, Any] | None" = None
    state: "Mapping[str, Any] | None" = None


class _ASGIResponseState:
    __slots__ = ("closed", "disconnected", "headers", "receiver", "sender", "started", "status_code")

    def __init__(self) -> None:
        self.status_code: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.sender, self.receiver = anyio.create_memory_object_stream[bytes](1)
        self.closed = False
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
        shutdown_timeout: "float",
        request: "httpx.Request",
    ) -> None:
        self._state = state
        self._task = task
        self._read_timeout = read_timeout
        self._shutdown_timeout = shutdown_timeout
        self._request = request

    async def __aiter__(self) -> "AsyncIterator[bytes]":
        while True:
            try:
                chunk = await _wait(self._state.receiver.receive(), self._read_timeout, self._request)
            except anyio.EndOfStream:
                break
            yield chunk
        await asyncio.shield(self._task)

    async def aclose(self) -> None:
        await _shutdown_app_task(self._state, self._task, self._shutdown_timeout)


def _retrieve_app_exception(task: "asyncio.Task[None]") -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        _logger.error("ASGI application failed after incomplete cleanup", exc_info=error)


async def _shutdown_app_task(
    state: "_ASGIResponseState", task: "asyncio.Task[None]", shutdown_timeout: "float"
) -> None:
    if state.closed:
        return
    state.closed = True
    state.receiver.close()
    state.disconnected.set()
    if not task.done():
        task.cancel()
    deadline = asyncio.get_running_loop().time() + shutdown_timeout
    cancelled = False
    with anyio.CancelScope(shield=True):
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                _logger.warning("ASGI response cleanup incomplete after %s seconds", shutdown_timeout)
                task.cancel()
                task.add_done_callback(_retrieve_app_exception)
                break
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError:
                cancelled = True
        if task.done():
            with contextlib.suppress(asyncio.CancelledError):
                task.result()
    if cancelled:
        raise asyncio.CancelledError


def _build_scope(request: "httpx.Request", client: "tuple[str, int]", root_path: "str") -> "dict[str, Any]":
    server_port = request.url.port or {"http": 80, "https": 443}.get(request.url.scheme)
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
        "server": (request.url.host, server_port),
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
        shutdown_timeout: Positive finite seconds allowed for application cleanup.
            Expiry is logged; cancellation-resistant application code may outlive
            the response.
    """

    def __init__(
        self,
        app: "ASGIApp",
        *,
        client: "tuple[str, int]" = _DEFAULT_CLIENT,
        root_path: "str" = "",
        shutdown_timeout: "float" = 5.0,
    ) -> None:
        if not math.isfinite(shutdown_timeout) or shutdown_timeout <= 0:
            msg = "shutdown_timeout must be positive and finite"
            raise ValueError(msg)
        self._app = app
        self._client = client
        self._root_path = root_path
        self._shutdown_timeout = shutdown_timeout

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
                    try:
                        await state.sender.send(body)
                    except (anyio.BrokenResourceError, anyio.ClosedResourceError) as exc:
                        if state.closed:
                            raise asyncio.CancelledError from exc
                        raise
                if not message.get("more_body", False):
                    state.sender.close()

        async def run_app() -> None:
            try:
                await self._app(cast("Any", scope), cast("Any", receive), cast("Any", send))
            finally:
                state.started.set()
                state.sender.close()

        read_timeout = cast("float | None", request.extensions.get("timeout", {}).get("read"))
        task = asyncio.create_task(run_app())
        try:
            await _wait(state.started.wait(), read_timeout, request)
        except BaseException:
            await _shutdown_app_task(state, task, self._shutdown_timeout)
            raise
        if state.status_code is None:
            await task
            msg = "ASGI application completed without sending http.response.start"
            raise RuntimeError(msg)
        return httpx.Response(
            state.status_code,
            headers=state.headers,
            stream=_ASGIResponseStream(
                state, task, read_timeout=read_timeout, shutdown_timeout=self._shutdown_timeout, request=request
            ),
            request=request,
        )


def _seed_stdio_identity(app: "ASGIApp", context: "MCPStdioContext") -> "ASGIApp":
    async def asgi(scope: "Any", receive: "Any", send: "Any") -> None:
        if scope["type"] == "http":
            scope["user"] = dict(context.user) if isinstance(context.user, Mapping) else context.user
            scope["auth"] = dict(context.auth) if isinstance(context.auth, Mapping) else context.auth
            scope["session"] = dict(context.session or {})
            scope["state"] = dict(context.state or {})
            if context.owner_id is not None:
                scope[MCP_OWNER_ID_SCOPE_KEY] = context.owner_id
        await app(scope, receive, send)

    return asgi


def _wraps_only(exc: "BaseException", target: "BaseException") -> "bool":
    """Return whether ``exc`` is ``target`` or an exception group holding nothing else."""
    if exc is target:
        return True
    members = getattr(exc, "exceptions", None)
    if not isinstance(members, tuple):
        return False
    return all(_wraps_only(member, target) for member in members)


@contextlib.asynccontextmanager
async def _app_lifespan(app: "Litestar", *, shutdown_timeout: "float" = 5.0) -> "AsyncIterator[None]":
    """Bound post-startup shutdown in the same task and preserve body failures.

    Startup unwind retains Litestar's native exception and cancellation
    semantics. Application hooks must bound and shield their own rollback
    where needed; the cleanup deadline is armed only after lifespan entry.
    """
    if not math.isfinite(shutdown_timeout) or shutdown_timeout <= 0:
        msg = "shutdown_timeout must be positive and finite"
        raise ValueError(msg)
    body_error: BaseException | None = None
    with anyio.CancelScope() as cleanup_scope:
        try:
            async with app.lifespan():
                try:
                    yield
                except BaseException as exc:
                    body_error = exc
                    raise
                finally:
                    cleanup_scope.shield = True
                    cleanup_scope.deadline = anyio.current_time() + shutdown_timeout
        except BaseException as exc:
            # A body failure is re-raised below, outside this handler, so the
            # exception keeps its own cause and context instead of gaining the
            # lifespan's wrapping group as implicit context.
            if body_error is None:
                raise
            if not _wraps_only(exc, body_error):
                _logger.warning("Lifespan shutdown failed after a body error", exc_info=True)
        finally:
            if cleanup_scope.cancel_called:
                _logger.warning("Lifespan shutdown incomplete after %s seconds", shutdown_timeout)
    if body_error is not None:
        raise body_error


def _resolve_plugin(app: "Litestar") -> "Any":
    from litestar_mcp.mcp.plugin import LitestarMCP

    try:
        return app.plugins.get(LitestarMCP)
    except KeyError as exc:
        msg = "The LitestarMCP plugin is not installed on this application"
        raise RuntimeError(msg) from exc


async def run_stdio_async(
    app: "Litestar",
    *,
    stdio_context: "MCPStdioContext | None" = None,
    headers: "Mapping[str, str] | None" = None,
    token_provider: "TokenProvider | None" = None,
    header_name: "str" = DEFAULT_AUTH_HEADER_NAME,
    token_prefix: "str" = BEARER_TOKEN_PREFIX,
    sse_read_timeout: "float | None" = 300.0,
    stdin: "ByteReceiveStream | None" = None,
    stdout: "ByteSendStream | None" = None,
    stderr: "Any | None" = None,
    max_message_size: "int" = DEFAULT_MAX_STDIN_MESSAGE_SIZE,
    shutdown_timeout: "float" = 5.0,
) -> "int":
    """Serve a Litestar MCP endpoint to a local stdio client without a socket.

    ``shutdown_timeout`` bounds in-process request cleanup and application
    shutdown after native lifespan entry succeeds. Bridge errors and body
    cancellation are preserved if that shutdown fails or times out.

    Startup and its unwind follow Litestar's native semantics, including
    exception grouping and chaining. Application startup, lifespan, and
    shutdown hooks must bound and shield their own cleanup where needed.
    """
    plugin = _resolve_plugin(app)
    endpoint = f"http://mcp-stdio/{plugin.config.base_path.strip('/')}"
    asgi_app = app if stdio_context is None else _seed_stdio_identity(app, stdio_context)
    client_info = None if stdio_context is None else {"name": stdio_context.client_id, "version": __version__}
    async with _app_lifespan(app, shutdown_timeout=shutdown_timeout):
        return await run_stdio_streamable_http_bridge(
            endpoint,
            transport=ASGIStreamingTransport(asgi_app, shutdown_timeout=shutdown_timeout),
            client_info=client_info,
            headers=headers,
            token_provider=token_provider,
            header_name=header_name,
            token_prefix=token_prefix,
            sse_read_timeout=sse_read_timeout,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            max_message_size=max_message_size,
        )


def run_stdio(
    app: "Litestar",
    *,
    stdio_context: "MCPStdioContext | None" = None,
    headers: "Mapping[str, str] | None" = None,
    token_provider: "TokenProvider | None" = None,
    header_name: "str" = DEFAULT_AUTH_HEADER_NAME,
    token_prefix: "str" = BEARER_TOKEN_PREFIX,
    sse_read_timeout: "float | None" = 300.0,
    stdin: "ByteReceiveStream | None" = None,
    stdout: "ByteSendStream | None" = None,
    stderr: "Any | None" = None,
    max_message_size: "int" = DEFAULT_MAX_STDIN_MESSAGE_SIZE,
    shutdown_timeout: "float" = 5.0,
) -> "int":
    """Synchronously run :func:`run_stdio_async`; ``KeyboardInterrupt`` exits 0."""
    try:
        return asyncio.run(
            run_stdio_async(
                app,
                stdio_context=stdio_context,
                headers=headers,
                token_provider=token_provider,
                header_name=header_name,
                token_prefix=token_prefix,
                sse_read_timeout=sse_read_timeout,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                max_message_size=max_message_size,
                shutdown_timeout=shutdown_timeout,
            )
        )
    except KeyboardInterrupt:
        return 0
