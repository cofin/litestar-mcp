"""Stdio to Streamable HTTP bridge for Litestar MCP endpoints."""

import asyncio
import base64
import inspect
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Mapping
from types import TracebackType
from typing import Any

import anyio
import httpx
from anyio import EndOfStream, get_cancelled_exc_class
from anyio.abc import ByteReceiveStream, ByteSendStream
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from litestar.serialization import decode_json, encode_json
from litestar.status_codes import HTTP_202_ACCEPTED, HTTP_401_UNAUTHORIZED
from typing_extensions import Self

from litestar_mcp.auth.backend import BEARER_TOKEN_PREFIX, DEFAULT_AUTH_HEADER_NAME
from litestar_mcp.exceptions import BridgeConnectionError, BridgeMessageTooLargeError, MissingDependencyError
from litestar_mcp.jsonrpc import JSONRPCError, error_response
from litestar_mcp.routes import (
    MCP_METHOD_HEADER,
    MCP_NAME_HEADER,
    MCP_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
)

TokenProvider = Callable[[], str] | Callable[[], Awaitable[str]]
BRIDGE_ERROR = -32001
DEFAULT_MAX_STDIN_MESSAGE_SIZE = 16 * 1024 * 1024
_MIN_VISIBLE_ASCII = 0x20
_MAX_VISIBLE_ASCII = 0x7E

__all__ = (
    "BRIDGE_ERROR",
    "DEFAULT_MAX_STDIN_MESSAGE_SIZE",
    "BridgeConnectionError",
    "BridgeMessageTooLargeError",
    "MissingDependencyError",
    "TokenProvider",
    "run_bridge",
    "run_stdio_streamable_http_bridge",
)


async def run_stdio_streamable_http_bridge(
    endpoint: str,
    *,
    headers: Mapping[str, str] | None = None,
    token_provider: TokenProvider | None = None,
    header_name: str = DEFAULT_AUTH_HEADER_NAME,
    token_prefix: str = BEARER_TOKEN_PREFIX,
    timeout: float = 30.0,  # noqa: ASYNC109
    sse_read_timeout: float | None = 300.0,
    stdin: ByteReceiveStream | None = None,
    stdout: ByteSendStream | None = None,
    stderr: Any | None = None,
    max_message_size: int = DEFAULT_MAX_STDIN_MESSAGE_SIZE,
) -> int:
    """Bridge local stdio JSON-RPC to a Litestar Streamable HTTP MCP endpoint.

    Args:
        endpoint: Full MCP Streamable HTTP endpoint URL.
        headers: Static HTTP headers sent to the endpoint.
        token_provider: Optional callable that returns a fresh token per request.
        header_name: Header used for token auth.
        token_prefix: Prefix prepended to token values.
        timeout: HTTP connect, write, and pool timeout in seconds.
        sse_read_timeout: Read timeout for streaming SSE responses. ``None``
            or ``0`` disables quiet-period timeouts for server streams.
        stdin: Optional byte receive stream for tests or embedding.
        stdout: Optional byte send stream for tests or embedding.
        stderr: Optional diagnostic text stream. Defaults to ``sys.stderr``.
        max_message_size: Maximum bytes allowed for one newline-delimited
            stdin JSON-RPC message. Set to ``-1`` to disable the limit.

    Returns:
        Process-style exit code. ``0`` means clean EOF/shutdown; non-zero means
        a transport or pump error was surfaced to the local stdio client.
    """
    event_source_cls = _load_event_source()
    stdin_stream: ByteReceiveStream = stdin if stdin is not None else _StdinByteReceiveStream()
    stdout_stream: ByteSendStream = stdout if stdout is not None else _StdoutByteSendStream()
    stderr_stream = stderr or sys.stderr
    auth = (
        _TokenProviderAuth(token_provider, header_name=header_name, token_prefix=token_prefix)
        if token_provider is not None
        else None
    )
    error: BaseException | None = None

    try:
        async with (
            _StreamableHTTPBridgeClient(
                endpoint,
                headers=headers,
                auth=auth,
                timeout=timeout,
                sse_read_timeout=_normalize_sse_read_timeout(sse_read_timeout),
                stdout=stdout_stream,
                stderr=stderr_stream,
                event_source_cls=event_source_cls,
            ) as bridge_client,
            anyio.create_task_group() as task_group,
        ):

            def record_error(exc: BaseException) -> None:
                nonlocal error
                if error is None:
                    error = exc

            task_group.start_soon(
                _run_bridge_pump,
                _pump_stdin_to_remote(
                    stdin_stream,
                    bridge_client,
                    max_message_size=max_message_size,
                ),
                task_group.cancel_scope,
                record_error,
                True,
            )
    except MissingDependencyError:
        raise
    except Exception as exc:  # noqa: BLE001
        error = exc

    if error is None:
        return 0

    await _write_bridge_error(stdout_stream, str(error))
    print(f"litestar mcp bridge transport error: {error}", file=stderr_stream)
    return 1


def run_bridge(
    endpoint: str,
    *,
    headers: Mapping[str, str] | None = None,
    token_provider: TokenProvider | None = None,
    header_name: str = DEFAULT_AUTH_HEADER_NAME,
    token_prefix: str = BEARER_TOKEN_PREFIX,
    timeout: float = 30.0,
    sse_read_timeout: float | None = 300.0,
    stdout: ByteSendStream | None = None,
    stderr: Any | None = None,
    max_message_size: int = DEFAULT_MAX_STDIN_MESSAGE_SIZE,
) -> int:
    """Synchronously run the stdio bridge for CLI integrations."""
    try:
        return asyncio.run(
            run_stdio_streamable_http_bridge(
                endpoint,
                headers=headers,
                token_provider=token_provider,
                header_name=header_name,
                token_prefix=token_prefix,
                timeout=timeout,
                sse_read_timeout=sse_read_timeout,
                stdout=stdout,
                stderr=stderr,
                max_message_size=max_message_size,
            )
        )
    except KeyboardInterrupt:
        return 0


class _TokenProviderAuth(httpx.Auth):
    """Resolve a bearer-style token per request and retry once on 401."""

    def __init__(self, token_provider: TokenProvider, *, header_name: str, token_prefix: str) -> None:
        self._token_provider = token_provider
        self._header_name = header_name
        self._token_prefix = token_prefix

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers[self._header_name] = await self._header_value()
        response = yield request
        if response.status_code == HTTP_401_UNAUTHORIZED:
            await response.aread()
            request.headers[self._header_name] = await self._header_value()
            yield request

    async def _header_value(self) -> str:
        if inspect.iscoroutinefunction(self._token_provider):
            token = await self._token_provider()
        else:
            token = await run_sync_in_worker_thread(self._token_provider)
        if inspect.isawaitable(token):
            token = await token
        return f"{self._token_prefix}{token}"


class _StreamableHTTPBridgeClient:
    """Minimal Streamable HTTP client for the stdio bridge."""

    def __init__(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str] | None,
        auth: httpx.Auth | None,
        timeout: float,
        sse_read_timeout: float | None,
        stdout: ByteSendStream,
        stderr: Any,
        event_source_cls: type[Any],
    ) -> None:
        self._endpoint = endpoint
        self._stdout = stdout
        self._stderr = stderr
        self._event_source_cls = event_source_cls
        self._client = httpx.AsyncClient(
            headers=dict(headers or {}),
            timeout=httpx.Timeout(timeout, read=sse_read_timeout),
            auth=auth,
            follow_redirects=True,
        )
        self._tool_headers: dict[str, list[tuple[tuple[str, ...], str]]] | None = None
        self._tool_headers_lock = asyncio.Lock()
        self._inflight: dict[Any, anyio.CancelScope] = {}
        self._inflight_lock = asyncio.Lock()
        self._stdout_lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
        await self._client.__aexit__(exc_type, exc_val, exc_tb)

    async def close(self) -> None:
        async with self._inflight_lock:
            scopes = tuple(self._inflight.values())
            self._inflight.clear()
        for scope in scopes:
            scope.cancel()

    async def post_message(self, message: dict[str, Any]) -> None:
        if message.get("method") == "notifications/cancelled":
            params = message.get("params")
            if isinstance(params, dict):
                await self.cancel_request(params.get("requestId"))
            return
        prepared = self._prepare_message(message)
        headers = await self._mcp_headers(prepared)
        request_id = prepared.get("id")
        cancel_scope = anyio.CancelScope()
        if request_id is not None:
            async with self._inflight_lock:
                self._inflight[request_id] = cancel_scope
        try:
            with cancel_scope:
                try:
                    async with self._client.stream(
                        "POST",
                        self._endpoint,
                        json=prepared,
                        headers=headers,
                    ) as response:
                        if response.status_code == HTTP_202_ACCEPTED:
                            return
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if content_type.startswith("application/json"):
                            payload = decode_json(await response.aread())
                            async with self._stdout_lock:
                                await _write_json_line(self._stdout, payload)
                        elif content_type.startswith("text/event-stream"):
                            await self._consume_sse_response(response, expected_id=request_id)
                        else:
                            msg = f"Unexpected Streamable HTTP content type: {content_type or '<empty>'}"
                            raise RuntimeError(msg)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    raise BridgeConnectionError(self._endpoint) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BridgeConnectionError(self._endpoint) from exc
        finally:
            if request_id is not None:
                async with self._inflight_lock:
                    self._inflight.pop(request_id, None)

    async def cancel_request(self, request_id: Any) -> None:
        """Close the HTTP response stream associated with a stdio request."""
        async with self._inflight_lock:
            scope = self._inflight.get(request_id)
        if scope is not None:
            scope.cancel()

    async def _mcp_headers(self, message: dict[str, Any]) -> dict[str, str]:
        method = str(message.get("method", ""))
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            MCP_PROTOCOL_VERSION_HEADER: MCP_PROTOCOL_VERSION,
            MCP_METHOD_HEADER: method,
        }
        params = message.get("params")
        if isinstance(params, dict):
            name_field = {
                "tools/call": "name",
                "resources/read": "uri",
                "prompts/get": "name",
                "tasks/get": "taskId",
                "tasks/update": "taskId",
                "tasks/cancel": "taskId",
            }.get(method)
            if name_field is not None and isinstance(params.get(name_field), str):
                headers[MCP_NAME_HEADER] = _encode_header_value(params[name_field])
            if method == "tools/call":
                headers.update(await self._custom_tool_headers(params))
        return headers

    @staticmethod
    def _prepare_message(message: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(message)
        params = dict(prepared.get("params") or {})
        meta = dict(params.get("_meta") or {})
        meta.setdefault("io.modelcontextprotocol/protocolVersion", MCP_PROTOCOL_VERSION)
        meta.setdefault("io.modelcontextprotocol/clientCapabilities", {})
        meta.setdefault(
            "io.modelcontextprotocol/clientInfo",
            {"name": "litestar-mcp-stdio-bridge", "version": "0.12.0"},
        )
        params["_meta"] = meta
        prepared["params"] = params
        return prepared

    async def _custom_tool_headers(self, params: dict[str, Any]) -> dict[str, str]:
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            return {}
        if self._tool_headers is None:
            await self._load_tool_headers()
        headers: dict[str, str] = {}
        for path, header_name in (self._tool_headers or {}).get(tool_name, ()):
            value: Any = arguments
            for part in path:
                if not isinstance(value, dict) or part not in value:
                    break
                value = value[part]
            else:
                if value is not None:
                    rendered = ("true" if value else "false") if isinstance(value, bool) else str(value)
                    headers[f"Mcp-Param-{header_name}"] = _encode_header_value(rendered)
        return headers

    async def _load_tool_headers(self) -> None:
        async with self._tool_headers_lock:
            if self._tool_headers is not None:
                return
            cache: dict[str, list[tuple[tuple[str, ...], str]]] = {}
            cursor: str | None = None
            page = 0
            while True:
                params = {"cursor": cursor} if cursor is not None else {}
                request = self._prepare_message(
                    {
                        "jsonrpc": "2.0",
                        "id": f"litestar-mcp-schema-cache-{page}",
                        "method": "tools/list",
                        "params": params,
                    }
                )
                response = await self._client.post(
                    self._endpoint,
                    json=request,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        MCP_PROTOCOL_VERSION_HEADER: MCP_PROTOCOL_VERSION,
                        MCP_METHOD_HEADER: "tools/list",
                    },
                )
                response.raise_for_status()
                payload = decode_json(response.content)
                result = payload.get("result") if isinstance(payload, dict) else None
                for tool in result.get("tools", ()) if isinstance(result, dict) else ():
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                        cache[tool["name"]] = list(_iter_header_schema(tool.get("inputSchema")))
                cursor = result.get("nextCursor") if isinstance(result, dict) else None
                if not isinstance(cursor, str):
                    break
                page += 1
            self._tool_headers = cache

    async def _consume_sse_response(self, response: httpx.Response, *, expected_id: Any | None) -> None:
        event_source = self._event_source_cls(response)
        async for event in event_source.aiter_sse():
            if not event.data:
                continue
            payload = decode_json(event.data)
            async with self._stdout_lock:
                await _write_json_line(self._stdout, payload)
            if expected_id is not None and isinstance(payload, dict) and payload.get("id") == expected_id:
                return


class _StdinByteReceiveStream(ByteReceiveStream):
    async def receive(self, max_bytes: int = 65536) -> bytes:
        return await run_sync_in_worker_thread(sys.stdin.buffer.readline, max_bytes, abandon_on_cancel=True)

    async def aclose(self) -> None:
        return None


class _StdoutByteSendStream(ByteSendStream):
    async def send(self, item: bytes) -> None:
        def write() -> None:
            sys.stdout.buffer.write(item)
            sys.stdout.buffer.flush()

        await run_sync_in_worker_thread(write, abandon_on_cancel=True)

    async def aclose(self) -> None:
        return None


def _load_event_source() -> type[Any]:
    try:
        from httpx_sse import EventSource
    except ImportError as exc:
        raise MissingDependencyError(package="httpx-sse", extra="bridge") from exc
    return EventSource


async def _run_bridge_pump(
    awaitable: Awaitable[None],
    cancel_scope: anyio.CancelScope,
    record_error: Callable[[BaseException], None],
    terminal: bool,
) -> None:
    try:
        await awaitable
    except get_cancelled_exc_class():
        raise
    except Exception as exc:  # noqa: BLE001
        record_error(_unwrap_exception_group(exc))
        cancel_scope.cancel()
    else:
        if terminal:
            cancel_scope.cancel()


async def _pump_stdin_to_remote(
    stdin: ByteReceiveStream,
    bridge_client: _StreamableHTTPBridgeClient,
    *,
    max_message_size: int,
) -> None:
    async with anyio.create_task_group() as task_group:
        async for line in _iter_stdin_lines(stdin, max_message_size=max_message_size):
            if not line.strip():
                continue
            raw = decode_json(line)
            if not isinstance(raw, dict):
                msg = "JSON-RPC messages must be JSON objects"
                raise TypeError(msg)
            task_group.start_soon(bridge_client.post_message, raw)


async def _iter_stdin_lines(stdin: ByteReceiveStream, *, max_message_size: int) -> AsyncIterator[bytes]:
    buffer = b""
    while True:
        try:
            chunk = await stdin.receive(65536)
        except EndOfStream:
            chunk = b""
        if not chunk:
            if buffer:
                if max_message_size > 0 and len(buffer) > max_message_size:
                    raise BridgeMessageTooLargeError(max_message_size)
                yield buffer
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if max_message_size > 0 and len(line) + 1 > max_message_size:
                raise BridgeMessageTooLargeError(max_message_size)
            yield line + b"\n"
        if max_message_size > 0 and len(buffer) > max_message_size:
            raise BridgeMessageTooLargeError(max_message_size)


async def _write_bridge_error(stdout: ByteSendStream, message: str) -> None:
    await _write_json_line(stdout, error_response(None, JSONRPCError(code=BRIDGE_ERROR, message=message)))


async def _write_json_line(stdout: ByteSendStream, payload: dict[str, Any]) -> None:
    await stdout.send(encode_json(payload) + b"\n")


def _normalize_sse_read_timeout(value: float | None) -> float | None:
    return None if value is None or value <= 0 else value


def _unwrap_exception_group(exc: BaseException) -> BaseException:
    while exceptions := getattr(exc, "exceptions", None):
        exc = exceptions[0]
    return exc


def _encode_header_value(value: str) -> str:
    if (
        value
        and all(_MIN_VISIBLE_ASCII <= ord(char) <= _MAX_VISIBLE_ASCII for char in value)
        and not value.startswith("=?base64?")
    ):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?base64?{encoded}?="


def _iter_header_schema(schema: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield ``(property_path, header_name)`` pairs from an input schema."""
    if not isinstance(schema, dict):
        return
    header_name = schema.get("x-mcp-header")
    if isinstance(header_name, str):
        yield path, header_name
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            yield from _iter_header_schema(child, (*path, name))
