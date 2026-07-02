"""Integration tests for the stdio to Streamable HTTP bridge."""

import contextlib
import json
import socket
import threading
import time
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import uvicorn
from anyio.abc import ByteReceiveStream, ByteSendStream
from litestar import Litestar, get

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.bridge import run_stdio_streamable_http_bridge

if TYPE_CHECKING:
    from collections.abc import Iterator


class _BytesSink(ByteSendStream):
    def __init__(self) -> None:
        self.buffer = bytearray()

    async def send(self, item: "bytes") -> None:
        self.buffer.extend(item)

    async def aclose(self) -> None:
        return None


class _QueuedBytesSource(ByteReceiveStream):
    def __init__(self, *chunks: "bytes") -> None:
        self._chunks = list(chunks)

    async def receive(self, max_bytes: "int" = 65536) -> "bytes":
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    async def aclose(self) -> "None":
        return None


def _free_port() -> "int":
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _run_app(app: "Litestar") -> "Iterator[str]":
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/.well-known/mcp-server.json", timeout=0.2)
            if response.status_code == 200:
                yield base_url
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        msg = "test MCP server did not start"
        raise RuntimeError(msg)
    server.should_exit = True
    thread.join(timeout=5)


def _build_app(*, base_path: "str" = "/mcp") -> "Litestar":
    @get("/hello/{name:str}", mcp_tool="hello", sync_to_thread=False)
    def hello(name: "str") -> "dict[str, str]":
        return {"message": f"hello {name}"}

    return Litestar(route_handlers=[hello], plugins=[LitestarMCP(MCPConfig(base_path=base_path))])


def _rpc_line(method: "str", params: "dict[str, Any] | None" = None, *, msg_id: "int | None" = 1) -> "bytes":
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        payload["id"] = msg_id
    if params is not None:
        payload["params"] = params
    return json.dumps(payload).encode() + b"\n"


def _parse_stdout(stdout: "_BytesSink") -> "list[dict[str, Any]]":
    return [json.loads(line) for line in stdout.buffer.splitlines()]


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_calls_litestar_tool_through_custom_endpoint() -> "None":
    app = _build_app(base_path="/api/mcp")
    with _run_app(app) as base_url:
        stdout = _BytesSink()
        stdin = _QueuedBytesSource(
            _rpc_line(
                "initialize",
                {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "bridge-test"}},
                msg_id=1,
            ),
            _rpc_line("notifications/initialized", msg_id=None),
            _rpc_line("tools/list", msg_id=2),
            _rpc_line("tools/call", {"name": "hello", "arguments": {"name": "Ada"}}, msg_id=3),
        )

        exit_code = await run_stdio_streamable_http_bridge(
            f"{base_url}/api/mcp",
            stdin=stdin,
            stdout=stdout,
            timeout=5,
        )

    messages = _parse_stdout(stdout)
    assert exit_code == 0
    assert [message.get("id") for message in messages] == [1, 2, 3]
    assert messages[0]["result"]["serverInfo"]["name"]
    assert any(tool["name"] == "hello" for tool in messages[1]["result"]["tools"])
    content = messages[2]["result"]["content"][0]["text"]
    assert json.loads(content) == {"message": "hello Ada"}


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_surfaces_auth_failure_as_stdio_error() -> "None":
    app = _build_app()
    with _run_app(app) as base_url:
        stdout = _BytesSink()
        stdin = _QueuedBytesSource(
            _rpc_line(
                "tools/list",
                msg_id=1,
            )
        )

        exit_code = await run_stdio_streamable_http_bridge(
            f"{base_url}/mcp",
            stdin=stdin,
            stdout=stdout,
            timeout=5,
        )

    messages = _parse_stdout(stdout)
    assert exit_code == 1
    assert messages[0]["error"]["code"] == -32000
    assert "400 Bad Request" in messages[0]["error"]["message"]
