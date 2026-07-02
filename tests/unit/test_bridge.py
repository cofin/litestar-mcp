"""Tests for the stdio to Streamable HTTP bridge."""

import builtins
import io
import json
import threading
from types import TracebackType
from typing import Any

import anyio
import httpx
import pytest
from click import Context
from click.testing import CliRunner
from typing_extensions import Self

from tests.conftest import BridgeBlockingBytesSource, BridgeBytesSink, BridgeQueuedBytesSource


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
            "https://example.test/api/mcp",
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


def test_bridge_command_parses_headers_and_bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.bridge import bridge_command

    captured: dict[str, Any] = {}

    def fake_run_bridge(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setenv("MCP_TOKEN", "whole-token")
    monkeypatch.setattr("litestar_mcp.bridge.run_bridge", fake_run_bridge)

    result = CliRunner().invoke(
        bridge_command,
        [
            "--endpoint",
            "https://example.test/api/mcp",
            "--header",
            "X-Trace: abc",
            "--bearer-env",
            "MCP_TOKEN",
            "--header-name",
            "X-Goog-IAP-JWT-Assertion",
            "--token-prefix",
            "",
            "--timeout",
            "12",
            "--sse-read-timeout",
            "45",
        ],
    )

    assert result.exit_code == 0
    assert captured["endpoint"] == "https://example.test/api/mcp"
    assert captured["headers"] == {"X-Trace": "abc"}
    assert captured["header_name"] == "X-Goog-IAP-JWT-Assertion"
    assert captured["token_prefix"] == ""
    assert captured["timeout"] == 12
    assert captured["sse_read_timeout"] == 45
    assert captured["token_provider"]() == "whole-token"


def test_bridge_command_rejects_multiple_bearer_sources() -> None:
    from litestar_mcp.bridge import bridge_command

    result = CliRunner().invoke(
        bridge_command,
        [
            "--endpoint",
            "https://example.test/api/mcp",
            "--bearer-env",
            "MCP_TOKEN",
            "--bearer-cmd",
            "dma auth token",
        ],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_bridge_command_discover_sends_headers_and_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.bridge import bridge_command

    captured_headers: dict[str, str] = {}
    captured_endpoint: dict[str, str] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        captured_endpoint["url"] = url
        captured_headers.update(kwargs["headers"])
        return httpx.Response(
            200,
            content=b'{"endpoints":{"mcp":"https://example.test/custom/mcp"}}',
            request=httpx.Request("GET", url),
        )

    def fake_run_bridge(**kwargs: Any) -> int:
        captured_endpoint["bridge"] = kwargs["endpoint"]
        return 0

    monkeypatch.setenv("MCP_TOKEN", "secret-token")
    monkeypatch.setattr("litestar_mcp.bridge.httpx.get", fake_get)
    monkeypatch.setattr("litestar_mcp.bridge.run_bridge", fake_run_bridge)

    result = CliRunner().invoke(
        bridge_command,
        [
            "--endpoint",
            "https://example.test/something",
            "--discover",
            "--header",
            "X-Tenant: acme",
            "--bearer-env",
            "MCP_TOKEN",
        ],
    )

    assert result.exit_code == 0
    assert captured_endpoint == {
        "url": "https://example.test/.well-known/mcp-server.json",
        "bridge": "https://example.test/custom/mcp",
    }
    assert captured_headers["X-Tenant"] == "acme"
    assert captured_headers["Authorization"] == "Bearer secret-token"


def test_bridge_command_missing_bridge_extra_is_clean_click_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.bridge import bridge_command

    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx_sse":
            msg = "No module named 'httpx_sse'"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = CliRunner().invoke(
        bridge_command,
        [
            "--endpoint",
            "https://example.test/api/mcp",
        ],
    )

    assert result.exit_code == 1
    assert "litestar-mcp[bridge]" in result.output
    assert "Traceback" not in result.output


@pytest.mark.anyio
async def test_bridge_get_stream_405_is_non_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp import bridge

    requests: list[tuple[str, str]] = []
    real_async_client = httpx.AsyncClient

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

    def fake_async_client(**kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(
            transport=httpx.MockTransport(handler),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            auth=kwargs.get("auth"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr("litestar_mcp.bridge.httpx.AsyncClient", fake_async_client)
    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        "https://example.test/api/mcp",
        stdin=BridgeQueuedBytesSource(
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
        ),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert ("GET", "https://example.test/api/mcp") in requests
    assert [json.loads(line)["id"] for line in stdout.buffer.splitlines()] == [1, 2]
    assert "server does not offer a GET SSE stream" in stderr.getvalue()


@pytest.mark.anyio
async def test_bridge_uses_exact_custom_endpoint_for_all_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp import bridge

    requests: list[tuple[str, str]] = []
    real_async_client = httpx.AsyncClient

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

    def fake_async_client(**kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(
            transport=httpx.MockTransport(handler),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            auth=kwargs.get("auth"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr("litestar_mcp.bridge.httpx.AsyncClient", fake_async_client)

    exit_code = await bridge.run_stdio_streamable_http_bridge(
        "https://example.test/api/mcp",
        stdin=BridgeQueuedBytesSource(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'),
        stdout=BridgeBytesSink(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert requests == [
        ("POST", "https://example.test/api/mcp"),
        ("DELETE", "https://example.test/api/mcp"),
    ]


@pytest.mark.anyio
async def test_bridge_clean_eof_exits_zero_without_error() -> None:
    from litestar_mcp.bridge import run_stdio_streamable_http_bridge

    stdout = BridgeBytesSink()
    stderr = io.StringIO()

    with anyio.fail_after(1):
        exit_code = await run_stdio_streamable_http_bridge(
            "https://example.test/api/mcp",
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

    real_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    def fake_async_client(**kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(
            transport=httpx.MockTransport(handler),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            auth=kwargs.get("auth"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr("litestar_mcp.bridge.httpx.AsyncClient", fake_async_client)
    client = bridge._StreamableHTTPBridgeClient(
        "https://example.test/api/mcp",
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
            "https://example.test/api/mcp",
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )

    assert exit_code == 1
    assert stdin.receive_started.is_set()
    assert "remote transport failed" in stderr.getvalue()
    [line] = stdout.buffer.splitlines()
    payload = json.loads(line)
    assert payload == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32001, "message": "remote transport failed"},
    }


def test_console_group_exposes_reexportable_bridge_command() -> None:
    from litestar_mcp.bridge import bridge_command
    from litestar_mcp.cli import litestar_mcp_group

    with Context(litestar_mcp_group) as ctx:
        assert litestar_mcp_group.get_command(ctx, "bridge") is bridge_command
