"""Tests for the stdio to Streamable HTTP bridge."""

from __future__ import annotations

import builtins
import io
import json
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
from anyio.abc import ByteReceiveStream, ByteSendStream
from click import Context
from click.testing import CliRunner
from typing_extensions import Self

if TYPE_CHECKING:
    from types import TracebackType


class _BytesSink(ByteSendStream):
    def __init__(self) -> None:
        self.buffer = bytearray()

    async def send(self, item: bytes) -> None:
        self.buffer.extend(item)

    async def aclose(self) -> None:
        return None


class _BlockingBytesSource(ByteReceiveStream):
    receive_started: anyio.Event

    def __init__(self) -> None:
        self.receive_started = anyio.Event()

    async def receive(self, max_bytes: int = 65536) -> bytes:
        self.receive_started.set()
        await anyio.sleep_forever()
        return b""

    async def aclose(self) -> None:
        return None


class _QueuedBytesSource(ByteReceiveStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = list(chunks)
        self.receive_started = anyio.Event()

    async def receive(self, max_bytes: int = 65536) -> bytes:
        self.receive_started.set()
        if self._chunks:
            return self._chunks.pop(0)
        await anyio.sleep_forever()
        return b""

    async def aclose(self) -> None:
        return None


@pytest.mark.anyio
async def test_missing_bridge_extra_error_names_install_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from litestar_mcp.bridge import MissingDependencyError, run_stdio_streamable_http_bridge

    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx_sse":
            msg = "No module named 'httpx_sse'"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(MissingDependencyError, match=r"litestar-mcp\[bridge\]"):
        await run_stdio_streamable_http_bridge(
            "https://example.test/api/mcp",
            stdin=_BlockingBytesSource(),
            stdout=_BytesSink(),
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
        ],
    )

    assert result.exit_code == 0
    assert captured["endpoint"] == "https://example.test/api/mcp"
    assert captured["headers"] == {"X-Trace": "abc"}
    assert captured["header_name"] == "X-Goog-IAP-JWT-Assertion"
    assert captured["token_prefix"] == ""
    assert captured["timeout"] == 12
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


@pytest.mark.anyio
async def test_bridge_reports_remote_stream_exception_and_cancels_stdin_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from litestar_mcp import bridge

    stdin = _QueuedBytesSource(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    stdout = _BytesSink()
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
        "error": {"code": -32000, "message": "remote transport failed"},
    }


def test_console_group_exposes_reexportable_bridge_command() -> None:
    from litestar_mcp.bridge import bridge_command
    from litestar_mcp.cli import litestar_mcp_group

    with Context(litestar_mcp_group) as ctx:
        assert litestar_mcp_group.get_command(ctx, "bridge") is bridge_command
