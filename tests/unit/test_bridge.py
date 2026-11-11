"""Tests for the stdio to Streamable HTTP bridge."""

import io
import json
import threading
from collections.abc import AsyncIterator, Callable
from types import TracebackType
from typing import Any

import anyio
import httpx2
import pytest
from litestar import Litestar, get
from litestar.connection import Request  # noqa: TC002
from typing_extensions import Self

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.core.serialization import decode_json, encode_json
from litestar_mcp.mcp.bridge import run_stdio_streamable_http_bridge
from litestar_mcp.mcp.routes import MCP_SESSION_ID_HEADER
from litestar_mcp.mcp.service import get_session_store
from litestar_mcp.mcp.stdio import ASGIStreamingTransport
from tests.conftest import BridgeBytesSink, BridgeQueuedBytesSource

ENDPOINT = "https://example.test/api/mcp"


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], Any]) -> None:
    real_async_client = httpx2.AsyncClient

    def fake_async_client(**kwargs: Any) -> httpx2.AsyncClient:
        return real_async_client(
            transport=httpx2.MockTransport(handler),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            auth=kwargs.get("auth"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr("litestar_mcp.mcp.bridge.httpx2.AsyncClient", fake_async_client)


def _assert_bridge_jsonrpc_error(stdout: BridgeBytesSink, message: str) -> None:
    assert json.loads(stdout.buffer) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32001, "message": message},
    }


@pytest.mark.anyio
async def test_token_provider_auth_resolves_fresh_token_and_retries_one_401() -> None:
    from litestar_mcp.mcp.bridge import _TokenProviderAuth

    tokens = iter(["first", "second", "third", "fourth"])
    seen_auth: list[str] = []
    statuses = iter([401, 200, 401, 401])

    async def token_provider() -> str:
        return next(tokens)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen_auth.append(request.headers["Authorization"])
        return httpx2.Response(next(statuses), request=request)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.method, str(request.url)))
        payload = json.loads(request.content)
        return httpx2.Response(
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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.method, str(request.url)))
        payload = json.loads(request.content)
        return httpx2.Response(
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

    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
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
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}, request=request)

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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        if payload["id"] == 1:
            await anyio.sleep(0.05)
        return httpx2.Response(
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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        message = "connection refused"
        raise httpx2.ConnectError(message, request=request)

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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(
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


def _sse_result_handler(payload: str) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        request_id = json.loads(request.content)["id"]
        event = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"payload": payload}})
        return httpx2.Response(
            200,
            content=f"data: {event}\n\n".encode(),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    return handler


@pytest.mark.anyio
async def test_bridge_rejects_oversized_server_sent_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.mcp import bridge

    _patch_async_client(monkeypatch, _sse_result_handler("x" * 512))
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n'),
        stdout=BridgeBytesSink(),
        stderr=stderr,
        max_message_size=256,
    )

    assert exit_code == 1
    assert "256 byte limit" in stderr.getvalue()


@pytest.mark.anyio
async def test_bridge_default_limit_accepts_server_sent_events_over_one_mebibyte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from litestar_mcp.mcp import bridge

    payload = "x" * (2 * 1024 * 1024)
    _patch_async_client(monkeypatch, _sse_result_handler(payload))
    stdout = BridgeBytesSink()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        ENDPOINT,
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n'),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert json.loads(stdout.buffer)["result"]["payload"] == payload


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

    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(404, request=request)

    _patch_async_client(monkeypatch, handler)
    client = bridge._StreamableHTTPBridgeClient(
        ENDPOINT,
        headers=None,
        auth=None,
        timeout=1,
        sse_read_timeout=1,
        stdout=BridgeBytesSink(),
        stderr=io.StringIO(),
        max_event_size=None,
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

    class NeverEndingSSE(httpx2.AsyncByteStream):
        async def __aiter__(self) -> "AsyncIterator[bytes]":
            yield b'data: {"jsonrpc":"2.0","method":"notifications/subscriptions/acknowledged","params":{}}\n\n'
            await anyio.sleep_forever()

        async def aclose(self) -> None:
            closed.set()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        if payload["method"] == "tools/list":
            return httpx2.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}}, request=request
            )
        if payload["method"] == "nope":
            return httpx2.Response(
                404,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -32601, "message": "Method not found: nope"},
                },
                request=request,
            )
        if payload["method"] == "tools/call":
            return httpx2.Response(
                400,
                json={"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32602, "message": "Unknown tool"}},
                request=request,
            )
        return httpx2.Response(
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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        if payload["method"] == "tools/list":
            return httpx2.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}}, request=request
            )
        return httpx2.Response(502, text="bad gateway", headers={"content-type": "text/plain"}, request=request)

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

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"status_code": 401, "detail": "Unauthorized"}, request=request)

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


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 502])
async def test_bridge_undecodable_json_error_body_reports_http_status(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    from litestar_mcp.mcp import bridge

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, content=b"<html>not json</html>", headers={"content-type": "application/json"}, request=request
        )

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
    assert str(status) in lines[-1]["error"]["message"]


def _bridge_rpc_line(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    msg_id: int | None = None,
) -> bytes:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        payload["id"] = msg_id
    if params is not None:
        payload["params"] = params
    return encode_json(payload) + b"\n"


class _RecordingBridgeTransport(httpx2.AsyncBaseTransport):
    """Wrap `ASGIStreamingTransport` and capture `(method, headers, body)` tuples in request order."""

    def __init__(self, app: Litestar) -> None:
        self._inner = ASGIStreamingTransport(app)
        self.requests: list[tuple[str, httpx2.Headers, bytes]] = []
        self.session_ids: list[str] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        self.requests.append((request.method, httpx2.Headers(request.headers), body))
        response = await self._inner.handle_async_request(request)
        sid = response.headers.get(MCP_SESSION_ID_HEADER)
        if sid:
            self.session_ids.append(sid)
        return response


class _PhasedAckGatedSource(_AckGatedSource):
    """Deliver phase-1 chunks, wait for `phase1_gate`, deliver phase-2 chunks, then hold EOF until `eof_gate`."""

    def __init__(
        self,
        phase1: tuple[bytes, ...],
        phase2: tuple[bytes, ...],
        *,
        phase1_gate: anyio.Event,
        eof_gate: anyio.Event,
    ) -> None:
        super().__init__(*phase1, gate=eof_gate)
        self._phase2 = b"".join(phase2)
        self._phase1_gate = phase1_gate
        self._phase1_remaining = len(phase1)
        self._phase2_sent = False

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._phase1_remaining > 0:
            self._phase1_remaining -= 1
            return await super().receive(max_bytes)
        if not self._phase2_sent:
            await self._phase1_gate.wait()
            self._phase2_sent = True
            if self._phase2:
                return self._phase2
        return await super().receive(max_bytes)


class _Bridge2025Sink(BridgeBytesSink):
    """Track parsed JSON-RPC frames written by the bridge and fire phase/EOF readiness events."""

    def __init__(self) -> None:
        super().__init__()
        self.subscribed_update_seen = anyio.Event()
        self.all_complete = anyio.Event()

    def messages(self) -> list[dict[str, Any]]:
        return [decode_json(bytes(line)) for line in self.buffer.splitlines() if line.strip()]

    async def send(self, item: bytes) -> None:
        await super().send(item)
        parsed = self.messages()
        ids = {msg.get("id") for msg in parsed if "id" in msg}
        has_update = any(msg.get("method") == "notifications/resources/updated" for msg in parsed)
        if {1, 2, 3}.issubset(ids) and has_update:
            self.subscribed_update_seen.set()
        if {1, 2, 3, 4, 5}.issubset(ids) and has_update:
            self.all_complete.set()


@pytest.mark.anyio
async def test_bridge_2025_sse_readiness_sequential_subscribe_and_session_delete() -> None:
    @get("/emit", mcp_tool="emit")
    async def emit_update(request: "Request[Any, Any, Any]") -> dict[str, str]:
        mcp_plugin = request.app.plugins.get(LitestarMCP)
        await mcp_plugin.registry.notify_resource_updated("litestar://doc")
        return {"emitted": "litestar://doc"}

    plugin = LitestarMCP(MCPConfig(base_path="/mcp"))
    app = Litestar(route_handlers=[emit_update], plugins=[plugin])
    transport = _RecordingBridgeTransport(app)
    stdout = _Bridge2025Sink()
    stderr = io.StringIO()

    stdin = _PhasedAckGatedSource(
        phase1=(
            _bridge_rpc_line(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "bridge-test", "version": "1.0"},
                },
                msg_id=1,
            ),
            _bridge_rpc_line("notifications/initialized"),
            _bridge_rpc_line("resources/subscribe", {"uri": "litestar://doc"}, msg_id=2),
            _bridge_rpc_line("tools/call", {"name": "emit", "arguments": {}}, msg_id=3),
        ),
        phase2=(
            _bridge_rpc_line("resources/unsubscribe", {"uri": "litestar://doc"}, msg_id=4),
            _bridge_rpc_line("tools/call", {"name": "emit", "arguments": {}}, msg_id=5),
        ),
        phase1_gate=stdout.subscribed_update_seen,
        eof_gate=stdout.all_complete,
    )

    async with app.lifespan():
        with anyio.fail_after(5):
            exit_code = await run_stdio_streamable_http_bridge(
                "http://mcp-stdio/mcp",
                transport=transport,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        assert transport.session_ids
        created_sid = transport.session_ids[0]
        remaining = await get_session_store(app, plugin.config).get(created_sid, None)

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert remaining is None

    parsed = stdout.messages()
    by_id = {msg["id"]: msg for msg in parsed if "id" in msg}
    assert set(by_id) == {1, 2, 3, 4, 5}
    assert by_id[1]["result"]["protocolVersion"] == "2025-11-25"
    assert by_id[1]["result"]["capabilities"]["resources"]["subscribe"] is True
    assert "error" not in by_id[2]
    assert "error" not in by_id[4]

    updates = [msg for msg in parsed if msg.get("method") == "notifications/resources/updated"]
    assert updates == [
        {"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {"uri": "litestar://doc"}}
    ]
    assert "_meta" not in updates[0]["params"]

    methods = [method for method, _, _ in transport.requests]
    assert methods[0] == "POST"
    assert methods[1] == "GET"
    assert methods[-1] == "DELETE"
    assert transport.requests[1][1]["mcp-session-id"] == created_sid
    assert transport.requests[-1][1]["mcp-session-id"] == created_sid

    post_after_init = [(hdrs, decode_json(raw)) for m, hdrs, raw in transport.requests[2:] if m == "POST"]
    assert post_after_init
    assert all(hdrs["mcp-protocol-version"] == "2025-11-25" for hdrs, _ in post_after_init)
    assert all(hdrs["mcp-session-id"] == created_sid for hdrs, _ in post_after_init)
    assert all("mcp-method" not in hdrs and "mcp-name" not in hdrs for hdrs, _ in post_after_init)
    assert all("_meta" not in body.get("params", {}) for _, body in post_after_init)


@pytest.mark.anyio
async def test_bridge_2025_ignores_405_when_server_disables_get_sse() -> None:
    plugin = LitestarMCP(MCPConfig(base_path="/mcp", enable_get_sse=False))
    app = Litestar(plugins=[plugin])
    transport = _RecordingBridgeTransport(app)
    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    async with app.lifespan():
        with anyio.fail_after(5):
            exit_code = await run_stdio_streamable_http_bridge(
                "http://mcp-stdio/mcp",
                transport=transport,
                stdin=BridgeQueuedBytesSource(
                    _bridge_rpc_line(
                        "initialize",
                        {"protocolVersion": "2025-11-25", "capabilities": {}},
                        msg_id=1,
                    ),
                    _bridge_rpc_line("notifications/initialized"),
                    _bridge_rpc_line("ping", {}, msg_id=2),
                    _bridge_rpc_line("tools/list", {}, msg_id=3),
                ),
                stdout=stdout,
                stderr=stderr,
            )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    methods = [method for method, _, _ in transport.requests]
    assert "GET" in methods
    assert methods[-1] == "DELETE"
    by_id = {msg["id"]: msg for msg in (decode_json(bytes(line)) for line in stdout.buffer.splitlines() if line)}
    assert set(by_id) == {1, 2, 3}
