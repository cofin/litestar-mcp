"""Tests for the stdio to Streamable HTTP bridge."""

import builtins
import io
import json
import threading
from collections.abc import AsyncIterator, Callable
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

    monkeypatch.setattr("litestar_mcp.mcp.bridge.httpx.AsyncClient", fake_async_client)


def _assert_bridge_jsonrpc_error(stdout: BridgeBytesSink, message: str) -> None:
    assert json.loads(stdout.buffer) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32001, "message": message},
    }


@pytest.mark.anyio
async def test_missing_bridge_extra_error_names_install_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.core.exceptions import MissingDependencyError as SharedMissingDependencyError
    from litestar_mcp.mcp.bridge import MissingDependencyError, run_stdio_streamable_http_bridge

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
    from litestar_mcp.mcp.bridge import _TokenProviderAuth

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
    from litestar_mcp.mcp.bridge import _TokenProviderAuth

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
async def test_bridge_never_uses_legacy_get_or_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload.get("id"), "result": {"ok": True}},
            request=request,
        )

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(
            b'{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
        ),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert requests == [("POST", ENDPOINT), ("POST", ENDPOINT)]
    assert [json.loads(line)["id"] for line in stdout.buffer.splitlines()] == [1, 2]
    assert stderr.getvalue() == ""


@pytest.mark.anyio
async def test_bridge_uses_exact_custom_endpoint_for_all_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload.get("id"), "result": {"ok": True}},
            request=request,
        )

    _patch_async_client(monkeypatch, handler)

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}\n'),
        stdout=BridgeBytesSink(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert requests == [("POST", ENDPOINT)]


@pytest.mark.anyio
async def test_bridge_adds_modern_metadata_and_lazy_custom_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        result: dict[str, Any]
        if payload["method"] == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "greet",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "tenant": {"type": "string", "x-mcp-header": "Tenant"},
                            },
                        },
                    }
                ]
            }
        else:
            result = {"ok": True}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}, request=request)

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"greet","arguments":{"tenant":"租户"}}}\n'.encode()
        ),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert [json.loads(request.content)["method"] for request in requests] == ["tools/list", "tools/call"]
    call_request = requests[-1]
    call_payload = json.loads(call_request.content)
    assert call_request.headers["MCP-Protocol-Version"] == "2026-07-28"
    assert call_request.headers["Mcp-Method"] == "tools/call"
    assert call_request.headers["Mcp-Name"] == "greet"
    assert call_request.headers["Mcp-Param-Tenant"].startswith("=?base64?")
    assert call_payload["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"


@pytest.mark.anyio
async def test_bridge_forwards_independent_requests_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["id"] == 1:
            await anyio.sleep(0.05)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}},
            request=request,
        )

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"resources/list"}\n',
        ),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert [json.loads(line)["id"] for line in stdout.buffer.splitlines()] == [2, 1]


@pytest.mark.anyio
async def test_bridge_connection_error_is_clean_jsonrpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.core.exceptions import BridgeConnectionError
    from litestar_mcp.mcp import bridge

    async def handler(request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message, request=request)

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n'),
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
    from litestar_mcp.core.exceptions import BridgeMessageTooLargeError
    from litestar_mcp.mcp import bridge

    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n'),
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
    from litestar_mcp.mcp import bridge

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
            b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"payload":"larger-than-limit"}}\n',
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
    from litestar_mcp.mcp.bridge import run_stdio_streamable_http_bridge

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
async def test_bridge_close_does_not_issue_http_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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
    await client.close()
    assert requests == []


@pytest.mark.anyio
async def test_bridge_reports_post_exception_and_cancels_stdin_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from litestar_mcp.mcp import bridge

    stdin = BridgeQueuedBytesSource(
        b'{"jsonrpc":"2.0","id":1,"method":"server/discover"}\n',
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

        async def post_message(self, message: dict[str, Any]) -> None:
            msg = "remote transport failed"
            raise RuntimeError(msg)

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


class _AckGatedSource(BridgeQueuedBytesSource):
    """Yield the queued lines, then hold EOF until the acknowledgement has been written."""

    def __init__(self, *chunks: bytes, gate: anyio.Event) -> None:
        super().__init__(*chunks)
        self._gate = gate

    async def receive(self, max_bytes: int = 65536) -> bytes:
        chunk = await super().receive(max_bytes)
        if chunk:
            return chunk
        await self._gate.wait()
        return b""


class _AckSink(BridgeBytesSink):
    """Byte sink that flags the subscription acknowledgement line."""

    def __init__(self) -> None:
        super().__init__()
        self.acknowledged = anyio.Event()

    async def send(self, item: bytes) -> None:
        await super().send(item)
        if b"notifications/subscriptions/acknowledged" in item:
            self.acknowledged.set()


@pytest.mark.anyio
async def test_bridge_stdin_eof_closes_open_subscription_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp.bridge import run_stdio_streamable_http_bridge

    closed = anyio.Event()

    class NeverEndingSSE(httpx.AsyncByteStream):
        async def __aiter__(self) -> "AsyncIterator[bytes]":
            yield b'data: {"jsonrpc":"2.0","method":"notifications/subscriptions/acknowledged","params":{}}\n\n'
            await anyio.sleep_forever()

        async def aclose(self) -> None:
            closed.set()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=NeverEndingSSE(), request=request
        )

    _patch_async_client(monkeypatch, handler)
    stdout = _AckSink()
    stdin = _AckGatedSource(
        b'{"jsonrpc":"2.0","id":1,"method":"subscriptions/listen","params":{"notifications":{"toolsListChanged":true}}}\n',
        gate=stdout.acknowledged,
    )

    with anyio.fail_after(2):
        exit_code = await run_stdio_streamable_http_bridge(ENDPOINT, stdin=stdin, stdout=stdout)

    assert exit_code == 0
    assert closed.is_set()
    assert b"notifications/subscriptions/acknowledged" in stdout.buffer


@pytest.mark.anyio
async def test_bridge_forwards_jsonrpc_error_envelopes_and_keeps_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "tools/list":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}}, request=request
            )
        if payload["method"] == "nope":
            return httpx.Response(
                404,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -32601, "message": "Method not found: nope"},
                },
                request=request,
            )
        if payload["method"] == "tools/call":
            return httpx.Response(
                400,
                json={"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32602, "message": "Unknown tool"}},
                request=request,
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}}, request=request
        )

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(
            b'{"jsonrpc":"2.0","id":1,"method":"nope","params":{}}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"missing","arguments":{}}}\n',
            b'{"jsonrpc":"2.0","id":3,"method":"ping","params":{}}\n',
        ),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    messages = {message["id"]: message for message in (json.loads(line) for line in stdout.buffer.splitlines() if line)}
    assert messages[1]["error"]["code"] == -32601
    assert messages[2]["error"]["code"] == -32602
    assert messages[3]["result"] == {"ok": True}


@pytest.mark.anyio
async def test_bridge_non_json_http_error_is_still_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "tools/list":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}}, request=request
            )
        return httpx.Response(502, text="bad gateway", headers={"content-type": "text/plain"}, request=request)

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n'),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 1
    lines = [json.loads(line) for line in stdout.buffer.splitlines() if line]
    assert lines[-1]["error"]["code"] == -32001


@pytest.mark.anyio
async def test_bridge_json_http_error_without_envelope_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status_code": 401, "detail": "Unauthorized"}, request=request)

    _patch_async_client(monkeypatch, handler)
    stdout = BridgeBytesSink()
    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n'),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 1
    lines = [json.loads(line) for line in stdout.buffer.splitlines() if line]
    assert lines[-1]["error"]["code"] == -32001
    assert "401" in lines[-1]["error"]["message"]
