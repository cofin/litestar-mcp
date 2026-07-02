"""Stdio to Streamable HTTP bridge for remote MCP servers."""

from __future__ import annotations

import asyncio
import inspect
import os
import shlex
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

import anyio
import httpx
from anyio import EndOfStream, get_cancelled_exc_class
from anyio.abc import ByteReceiveStream, ByteSendStream
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from click.exceptions import Exit as ClickExit
from litestar.serialization import decode_json, encode_json
from litestar.status_codes import HTTP_202_ACCEPTED, HTTP_401_UNAUTHORIZED
from typing_extensions import Self

try:
    import rich_click as click
except ImportError:  # pragma: no cover
    import click  # type: ignore[no-redef]

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator
    from types import TracebackType

TokenProvider = Callable[[], str] | Callable[[], Awaitable[str]]
BRIDGE_ERROR = -32000
DEFAULT_TOKEN_PREFIX = "Bearer "  # noqa: S105

__all__ = (
    "BRIDGE_ERROR",
    "MissingDependencyError",
    "TokenProvider",
    "bridge_command",
    "run_bridge",
    "run_stdio_streamable_http_bridge",
)


class MissingDependencyError(ImportError):
    """Raised when the optional bridge dependency group is not installed."""

    def __init__(self) -> None:
        message = (
            "The stdio bridge requires the optional bridge dependency. "
            "Install it with `pip install 'litestar-mcp[bridge]'` or "
            "`uv add 'litestar-mcp[bridge]'`."
        )
        super().__init__(message)


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
        token = self._token_provider()
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
        stdout: ByteSendStream,
    ) -> None:
        self._endpoint = endpoint
        self._stdout = stdout
        self._client = httpx.AsyncClient(
            headers=dict(headers or {}),
            timeout=httpx.Timeout(timeout, read=timeout),
            auth=auth,
            follow_redirects=True,
        )
        self._session_id: str | None = None
        self._protocol_version: str | None = None

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
            response.raise_for_status()

    async def post_message(self, message: dict[str, Any], *, start_get_stream: Callable[[], None]) -> None:
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

            if message.get("method") == "notifications/initialized":
                start_get_stream()

    async def run_sse_stream(self) -> None:
        if self._session_id is None:
            return
        async with self._client.stream(
            "GET",
            self._endpoint,
            headers={**self._mcp_headers(), "Accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()
            await self._consume_sse_response(response, expected_id=None)

    def _mcp_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version is not None:
            headers["mcp-protocol-version"] = self._protocol_version
        return headers

    def _capture_headers(self, response: httpx.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        protocol_version = response.headers.get("mcp-protocol-version")
        if protocol_version:
            self._protocol_version = protocol_version

    def _capture_protocol_version(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            self._protocol_version = result["protocolVersion"]

    async def _consume_sse_response(self, response: httpx.Response, *, expected_id: Any | None) -> None:
        event_source_cls = _load_event_source()
        event_source = event_source_cls(response)
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
        raise MissingDependencyError from exc
    return EventSource


async def run_stdio_streamable_http_bridge(
    endpoint: str,
    *,
    headers: Mapping[str, str] | None = None,
    token_provider: TokenProvider | None = None,
    header_name: str = "Authorization",
    token_prefix: str = DEFAULT_TOKEN_PREFIX,
    timeout: float = 30.0,  # noqa: ASYNC109
    stdin: ByteReceiveStream | None = None,
    stdout: ByteSendStream | None = None,
    stderr: Any | None = None,
) -> int:
    """Bridge local stdio JSON-RPC to a remote Streamable HTTP MCP endpoint.

    Args:
        endpoint: Full MCP Streamable HTTP endpoint URL.
        headers: Static HTTP headers sent to the remote server.
        token_provider: Optional callable that returns a fresh token per request.
        header_name: Header used for token auth.
        token_prefix: Prefix prepended to token values.
        timeout: HTTP operation timeout in seconds.
        stdin: Optional byte receive stream for tests or embedding.
        stdout: Optional byte send stream for tests or embedding.
        stderr: Optional diagnostic text stream. Defaults to ``sys.stderr``.

    Returns:
        Process-style exit code. ``0`` means clean EOF/shutdown; non-zero means
        a transport or pump error was surfaced to the local stdio client.
    """
    _load_event_source()
    stdin_stream: ByteReceiveStream = stdin if stdin is not None else _StdinByteReceiveStream()
    stdout_stream: ByteSendStream = stdout if stdout is not None else _StdoutByteSendStream()
    stderr_stream = stderr or sys.stderr
    auth = (
        _TokenProviderAuth(token_provider, header_name=header_name, token_prefix=token_prefix)
        if token_provider is not None
        else None
    )
    errors: list[BaseException] = []

    try:
        async with _StreamableHTTPBridgeClient(
            endpoint,
            headers=headers,
            auth=auth,
            timeout=timeout,
            stdout=stdout_stream,
        ) as bridge_client:
            get_stream_started = False
            async with anyio.create_task_group() as task_group:

                def start_get_stream() -> None:
                    nonlocal get_stream_started
                    if get_stream_started:
                        return
                    get_stream_started = True
                    task_group.start_soon(
                        _run_bridge_pump,
                        bridge_client.run_sse_stream(),
                        task_group.cancel_scope,
                        errors,
                    )

                task_group.start_soon(
                    _run_bridge_pump,
                    _pump_stdin_to_remote(stdin_stream, bridge_client, start_get_stream),
                    task_group.cancel_scope,
                    errors,
                )
    except MissingDependencyError:
        raise
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)

    if not errors:
        return 0

    error = errors[0]
    await _write_bridge_error(stdout_stream, str(error))
    print(f"litestar-mcp bridge transport error: {error}", file=stderr_stream)
    return 1


def run_bridge(
    endpoint: str,
    *,
    headers: Mapping[str, str] | None = None,
    token_provider: TokenProvider | None = None,
    header_name: str = "Authorization",
    token_prefix: str = DEFAULT_TOKEN_PREFIX,
    timeout: float = 30.0,
) -> int:
    """Synchronously run the stdio bridge for console-script entry points."""
    try:
        return asyncio.run(
            run_stdio_streamable_http_bridge(
                endpoint,
                headers=headers,
                token_provider=token_provider,
                header_name=header_name,
                token_prefix=token_prefix,
                timeout=timeout,
            )
        )
    except KeyboardInterrupt:
        return 0


async def _run_bridge_pump(
    awaitable: Awaitable[None],
    cancel_scope: anyio.CancelScope,
    errors: list[BaseException],
) -> None:
    try:
        await awaitable
    except get_cancelled_exc_class():
        raise
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
        cancel_scope.cancel()
    else:
        cancel_scope.cancel()


async def _pump_stdin_to_remote(
    stdin: ByteReceiveStream,
    bridge_client: _StreamableHTTPBridgeClient,
    start_get_stream: Callable[[], None],
) -> None:
    async for line in _iter_stdin_lines(stdin):
        if not line.strip():
            continue
        raw = decode_json(line)
        if not isinstance(raw, dict):
            msg = "JSON-RPC messages must be JSON objects"
            raise TypeError(msg)
        await bridge_client.post_message(raw, start_get_stream=start_get_stream)


async def _iter_stdin_lines(stdin: ByteReceiveStream) -> AsyncIterator[bytes]:
    buffer = b""
    while True:
        try:
            chunk = await stdin.receive(65536)
        except EndOfStream:
            chunk = b""
        if not chunk:
            if buffer:
                yield buffer
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line + b"\n"


async def _write_bridge_error(stdout: ByteSendStream, message: str) -> None:
    await _write_json_line(
        stdout,
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": BRIDGE_ERROR, "message": message},
        },
    )


async def _write_json_line(stdout: ByteSendStream, payload: dict[str, Any]) -> None:
    await stdout.send(encode_json(payload) + b"\n")


def _parse_header(value: str) -> tuple[str, str]:
    if ":" not in value:
        msg = "Headers must use 'Name: value' format."
        raise click.BadParameter(msg)
    name, header_value = value.split(":", 1)
    name = name.strip()
    if not name:
        msg = "Header name cannot be empty."
        raise click.BadParameter(msg)
    return name, header_value.strip()


def _headers_from_options(values: tuple[str, ...]) -> dict[str, str]:
    return dict(_parse_header(value) for value in values)


def _token_provider_from_env(variable_name: str) -> Callable[[], str]:
    def provide_token() -> str:
        value = os.getenv(variable_name)
        if value is None:
            msg = f"Environment variable {variable_name!r} is not set."
            raise click.ClickException(msg)
        return value

    return provide_token


def _token_provider_from_cmd(command: str) -> Callable[[], str]:
    args = shlex.split(command)

    def provide_token() -> str:
        completed = subprocess.run(args, check=True, capture_output=True, text=True)  # noqa: S603
        return completed.stdout.strip()

    return provide_token


def _discover_endpoint(origin: str, *, timeout: float) -> str:
    url = httpx.URL(origin)
    root = f"{url.scheme}://{url.netloc.decode()}"
    manifest_url = f"{root}/.well-known/mcp-server.json"
    response = httpx.get(manifest_url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    endpoint = response.json().get("endpoints", {}).get("mcp")
    if not isinstance(endpoint, str) or not endpoint:
        msg = f"{manifest_url} did not include endpoints.mcp"
        raise click.ClickException(msg)
    return endpoint


@click.command(name="bridge")
@click.option("--endpoint", required=True, help="Full MCP Streamable HTTP endpoint URL.")
@click.option("--header", "header_values", multiple=True, help="Static HTTP header as 'Name: value'.")
@click.option("--bearer-env", help="Environment variable containing the bearer token.")
@click.option("--bearer-cmd", help="Command whose stdout returns the bearer token.")
@click.option("--header-name", default="Authorization", show_default=True, help="Header used for bearer tokens.")
@click.option(
    "--token-prefix", default=DEFAULT_TOKEN_PREFIX, show_default=True, help="Prefix prepended to bearer token values."
)
@click.option("--timeout", default=30.0, show_default=True, type=float, help="HTTP timeout in seconds.")
@click.option("--discover", is_flag=True, help="Resolve the endpoint from /.well-known/mcp-server.json.")
def bridge_command(
    endpoint: str,
    header_values: tuple[str, ...],
    bearer_env: str | None,
    bearer_cmd: str | None,
    header_name: str,
    token_prefix: str,
    timeout: float,
    discover: bool,
) -> None:
    """Proxy local stdio JSON-RPC to a remote MCP Streamable HTTP endpoint."""
    if bearer_env and bearer_cmd:
        msg = "--bearer-env and --bearer-cmd are mutually exclusive."
        raise click.UsageError(msg)

    headers = _headers_from_options(header_values)
    token_provider: TokenProvider | None = None
    if bearer_env:
        token_provider = _token_provider_from_env(bearer_env)
    elif bearer_cmd:
        token_provider = _token_provider_from_cmd(bearer_cmd)

    resolved_endpoint = _discover_endpoint(endpoint, timeout=timeout) if discover else endpoint
    exit_code = run_bridge(
        endpoint=resolved_endpoint,
        headers=headers,
        token_provider=token_provider,
        header_name=header_name,
        token_prefix=token_prefix,
        timeout=timeout,
    )
    raise ClickExit(exit_code)
