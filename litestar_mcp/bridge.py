"""Stdio to Streamable HTTP bridge for Litestar MCP endpoints."""

import asyncio
import inspect
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
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
from litestar_mcp.routes import MCP_PROTOCOL_VERSION_HEADER, MCP_SESSION_HEADER

TokenProvider = Callable[[], str] | Callable[[], Awaitable[str]]
BRIDGE_ERROR = -32001
DEFAULT_MAX_STDIN_MESSAGE_SIZE = 16 * 1024 * 1024
_SSE_RECONNECT_DELAY_SECONDS = 1.0

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
        async with _StreamableHTTPBridgeClient(
            endpoint,
            headers=headers,
            auth=auth,
            timeout=timeout,
            sse_read_timeout=_normalize_sse_read_timeout(sse_read_timeout),
            stdout=stdout_stream,
            stderr=stderr_stream,
            event_source_cls=event_source_cls,
        ) as bridge_client:
            get_stream_started = False
            async with anyio.create_task_group() as task_group:

                def record_error(exc: BaseException) -> None:
                    nonlocal error
                    if error is None:
                        error = exc

                def start_get_stream() -> None:
                    nonlocal get_stream_started
                    if get_stream_started:
                        return
                    get_stream_started = True
                    task_group.start_soon(
                        _run_bridge_pump,
                        bridge_client.run_sse_stream(),
                        task_group.cancel_scope,
                        record_error,
                        False,
                    )

                task_group.start_soon(
                    _run_bridge_pump,
                    _pump_stdin_to_remote(
                        stdin_stream,
                        bridge_client,
                        start_get_stream,
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
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._get_stream_disabled = False

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
        if self._session_id is None:
            return
        try:
            response = await self._client.delete(self._endpoint, headers=self._mcp_headers())
        except httpx.HTTPError:
            return
        if response.status_code not in (200, 204, 405):
            print(
                f"litestar mcp bridge ignored shutdown DELETE status {response.status_code}",
                file=self._stderr,
            )

    async def post_message(self, message: dict[str, Any], *, start_get_stream: Callable[[], None]) -> None:
        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                json=message,
                headers=self._mcp_headers(),
            ) as response:
                if response.status_code == HTTP_202_ACCEPTED:
                    if message.get("method") == "notifications/initialized":
                        start_get_stream()
                    return
                response.raise_for_status()
                self._capture_headers(response)
                content_type = response.headers.get("content-type", "").lower()
                if content_type.startswith("application/json"):
                    payload = decode_json(await response.aread())
                    self._capture_protocol_version(payload)
                    await _write_json_line(self._stdout, payload)
                elif content_type.startswith("text/event-stream"):
                    await self._consume_sse_response(response, expected_id=message.get("id"))
                else:
                    msg = f"Unexpected Streamable HTTP content type: {content_type or '<empty>'}"
                    raise RuntimeError(msg)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BridgeConnectionError(self._endpoint) from exc

        if message.get("method") == "notifications/initialized":
            start_get_stream()

    async def run_sse_stream(self) -> None:
        if self._session_id is None or self._get_stream_disabled:
            return
        while not self._get_stream_disabled:
            try:
                async with self._client.stream(
                    "GET",
                    self._endpoint,
                    headers={**self._mcp_headers(), "Accept": "text/event-stream"},
                ) as response:
                    if response.status_code in (404, 405):
                        self._get_stream_disabled = True
                        print(
                            "litestar mcp bridge: server does not offer a GET SSE stream; continuing with POST responses",
                            file=self._stderr,
                        )
                        return
                    response.raise_for_status()
                    await self._consume_sse_response(response, expected_id=None)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise BridgeConnectionError(self._endpoint) from exc
            await anyio.sleep(_SSE_RECONNECT_DELAY_SECONDS)

    def _mcp_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id is not None:
            headers[MCP_SESSION_HEADER] = self._session_id
        if self._protocol_version is not None:
            headers[MCP_PROTOCOL_VERSION_HEADER] = self._protocol_version
        return headers

    def _capture_headers(self, response: httpx.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        protocol_version = response.headers.get(MCP_PROTOCOL_VERSION_HEADER)
        if protocol_version:
            self._protocol_version = protocol_version

    def _capture_protocol_version(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            self._protocol_version = result["protocolVersion"]

    async def _consume_sse_response(self, response: httpx.Response, *, expected_id: Any | None) -> None:
        event_source = self._event_source_cls(response)
        async for event in event_source.aiter_sse():
            if not event.data:
                continue
            payload = decode_json(event.data)
            self._capture_protocol_version(payload)
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
        record_error(exc)
        cancel_scope.cancel()
    else:
        if terminal:
            cancel_scope.cancel()


async def _pump_stdin_to_remote(
    stdin: ByteReceiveStream,
    bridge_client: _StreamableHTTPBridgeClient,
    start_get_stream: Callable[[], None],
    *,
    max_message_size: int,
) -> None:
    async for line in _iter_stdin_lines(stdin, max_message_size=max_message_size):
        if not line.strip():
            continue
        raw = decode_json(line)
        if not isinstance(raw, dict):
            msg = "JSON-RPC messages must be JSON objects"
            raise TypeError(msg)
        await bridge_client.post_message(raw, start_get_stream=start_get_stream)


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
