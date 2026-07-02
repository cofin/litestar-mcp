"""Tests for the stdio to Streamable HTTP bridge."""

import builtins
import io
import json
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any

import anyio
import httpx
import pytest
from typing_extensions import Self

from tests.conftest import BridgeBlockingBytesSource, BridgeBytesSink, BridgeQueuedBytesSource

ENDPOINT = "https://example.test/api/mcp"


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], Any]) -> None:
    real_async_client = httpx.AsyncClient

    def fake_async_client(**kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(
            transport=httpx.MockTransport(handler),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            auth=kwargs.get("auth"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr("litestar_mcp.bridge.httpx.AsyncClient", fake_async_client)


def _assert_bridge_jsonrpc_error(stdout: BridgeBytesSink, message: str) -> None:
    assert json.loads(stdout.buffer) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32001, "message": message},
    }


@pytest.mark.anyio
async def test_missing_bridge_extra_error_names_install_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.bridge import MissingDependencyError, run_stdio_streamable_http_bridge
    from litestar_mcp.exceptions import MissingDependencyError as SharedMissingDependencyError

    assert MissingDependencyError is SharedMissingDependencyError

    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx_sse":
            msg = "No module named 'httpx_sse'"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(MissingDependencyError, match=r"Package 'httpx-sse'.*litestar-mcp\[bridge\]"):
        await run_stdio_streamable_http_bridge(
            ENDPOINT,
            stdin=BridgeBlockingBytesSource(),
            stdout=BridgeBytesSink(),
        )


@pytest.mark.anyio
async def test_token_provider_auth_resolves_fresh_token_and_retries_one_401() -> None:
    from litestar_mcp.bridge import _TokenProviderAuth

    tokens = iter(["first", "second", "third", "fourth"])
    seen_auth: list[str] = []
    statuses = iter([401, 200, 401, 401])

    async def token_provider() -> str:
        return next(tokens)

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["Authorization"])
        return httpx.Response(next(statuses), request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=_TokenProviderAuth(token_provider, header_name="Authorization", token_prefix="Bearer "),
    ) as client:
        first_response = await client.get("https://example.test/api/mcp")
        second_response = await client.get("https://example.test/api/mcp")

    assert first_response.status_code == 200
    assert second_response.status_code == 401
    assert seen_auth == [
        "Bearer first",
        "Bearer second",
        "Bearer third",
        "Bearer fourth",
    ]


@pytest.mark.anyio
async def test_token_provider_auth_offloads_sync_provider_from_event_loop() -> None:
    from litestar_mcp.bridge import _TokenProviderAuth

    event_loop_thread = threading.get_ident()
    provider_threads: list[int] = []

    def token_provider() -> str:
        provider_threads.append(threading.get_ident())
        return "token"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=_TokenProviderAuth(token_provider, header_name="Authorization", token_prefix="Bearer "),
    ) as client:
        response = await client.get("https://example.test/api/mcp")

    assert response.status_code == 200
    assert provider_threads
    assert all(thread_id != event_loop_thread for thread_id in provider_threads)


@pytest.mark.anyio
async def test_bridge_get_stream_405_is_non_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp import bridge

    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(405, request=request)
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        payload = json.loads(request.content)
        headers = {"mcp-session-id": "sid-1"} if payload.get("method") == "initialize" else {}
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202, headers=headers, request=request)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload.get("id"), "result": {"ok": True}},
            headers=headers,
            request=request,
        )

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
        ),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert ("GET", ENDPOINT) in requests
    assert [json.loads(line)["id"] for line in stdout.buffer.splitlines()] == [1, 2]
    assert "server does not offer a GET SSE stream" in stderr.getvalue()


@pytest.mark.anyio
async def test_bridge_uses_exact_custom_endpoint_for_all_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp import bridge

    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        payload = json.loads(request.content)
        headers = {"mcp-session-id": "sid-1"} if payload.get("method") == "initialize" else {}
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload.get("id"), "result": {"ok": True}},
            headers=headers,
            request=request,
        )

    _patch_async_client(monkeypatch, handler)

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'),
        stdout=BridgeBytesSink(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert requests == [
        ("POST", ENDPOINT),
        ("DELETE", ENDPOINT),
    ]


@pytest.mark.anyio
async def test_bridge_connection_error_is_clean_jsonrpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp import bridge
    from litestar_mcp.exceptions import BridgeConnectionError

    async def handler(request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message, request=request)

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'),
        stdout=stdout,
        stderr=stderr,
    )

    expected_message = str(BridgeConnectionError(ENDPOINT))
    assert exit_code == 1
    _assert_bridge_jsonrpc_error(stdout, expected_message)
    assert expected_message in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


@pytest.mark.anyio
async def test_bridge_rejects_oversized_stdin_message() -> None:
    from litestar_mcp import bridge
    from litestar_mcp.exceptions import BridgeMessageTooLargeError

    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'),
        stdout=stdout,
        stderr=stderr,
        max_message_size=8,
    )

    expected_message = str(BridgeMessageTooLargeError(8))
    assert exit_code == 1
    _assert_bridge_jsonrpc_error(stdout, expected_message)
    assert expected_message in stderr.getvalue()


@pytest.mark.anyio
async def test_bridge_max_message_size_minus_one_disables_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp import bridge

    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": requests[-1].get("id"), "result": {"ok": True}},
            request=request,
        )

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"payload":"larger-than-limit"}}\n',
        ),
        stdout=stdout,
        stderr=io.StringIO(),
        max_message_size=-1,
    )

    assert exit_code == 0
    assert requests[0]["params"]["payload"] == "larger-than-limit"
    assert json.loads(stdout.buffer)["id"] == 1


@pytest.mark.anyio
async def test_bridge_clean_eof_exits_zero_without_error() -> None:
    from litestar_mcp.bridge import run_stdio_streamable_http_bridge

    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    with anyio.fail_after(1):
        exit_code = await run_stdio_streamable_http_bridge(
            ENDPOINT,
            stdin=BridgeQueuedBytesSource(),
            stdout=stdout,
            stderr=stderr,
        )

    assert exit_code == 0
    assert stdout.buffer == b""
    assert stderr.getvalue() == ""


@pytest.mark.anyio
async def test_bridge_close_delete_status_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp import bridge

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    _patch_async_client(monkeypatch, handler)
    client = bridge._StreamableHTTPBridgeClient(
        ENDPOINT,
        headers=None,
        auth=None,
        timeout=1,
        sse_read_timeout=1,
        stdout=BridgeBytesSink(),
        stderr=io.StringIO(),
        event_source_cls=object,
    )
    client._session_id = "sid-1"

    await client.close()


@pytest.mark.anyio
async def test_bridge_reports_remote_stream_exception_and_cancels_stdin_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from litestar_mcp import bridge

    stdin = BridgeQueuedBytesSource(
        b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
        block_after_chunks=True,
    )
    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    class FailingBridgeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            return None

        async def run_sse_stream(self) -> None:
            msg = "remote transport failed"
            raise RuntimeError(msg)

        async def post_message(self, message: dict[str, Any], *, start_get_stream: Any) -> None:
            start_get_stream()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(bridge, "_StreamableHTTPBridgeClient", FailingBridgeClient)

    with anyio.fail_after(1):
        exit_code = await bridge.run_stdio_streamable_http_bridge(
            ENDPOINT,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )

    assert exit_code == 1
    assert stdin.receive_started.is_set()
    assert "remote transport failed" in stderr.getvalue()
    _assert_bridge_jsonrpc_error(stdout, "remote transport failed")
