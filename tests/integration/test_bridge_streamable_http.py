"""Integration tests for the stdio to Streamable HTTP bridge."""

import contextlib
import io
import json
import logging
import socket
import threading
import time
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
import uvicorn
from anyio import sleep_forever
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from litestar import Litestar, get
from litestar.middleware import DefineMiddleware

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthBackend
from litestar_mcp.bridge import run_stdio_streamable_http_bridge
from tests.conftest import BridgeBytesSink, BridgeQueuedBytesSource
from tests.integration._auth import (
    FORGED_TOKEN,
    AuthenticatedUser,
    bearer_token_validator,
    build_mcp_auth_config,
    mint_access_token,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from litestar.connection import Request


def _free_port() -> "int":
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _run_app(app: "Litestar") -> "Iterator[str]":
    with _run_app_server(app) as (base_url, _server):
        yield base_url


@contextlib.contextmanager
def _run_app_server(app: "Litestar") -> "Iterator[tuple[str, uvicorn.Server]]":
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
            timeout_graceful_shutdown=0,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            httpx.get(base_url, timeout=0.2)
            yield base_url, server
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        msg = "test MCP server did not start"
        raise RuntimeError(msg)
    server.should_exit = True
    server.force_exit = True
    thread.join(timeout=5)


def _build_app(*, base_path: "str" = "/mcp") -> "Litestar":
    @get("/hello/{name:str}", mcp_tool="hello", sync_to_thread=False)
    def hello(name: "str") -> "dict[str, str]":
        return {"message": f"hello {name}"}

    return Litestar(route_handlers=[hello], plugins=[LitestarMCP(MCPConfig(base_path=base_path))])


def _build_auth_app() -> "Litestar":
    async def resolve_user(claims: "dict[str, Any]", _app: "Any") -> "AuthenticatedUser":
        return AuthenticatedUser(sub=str(claims.get("sub", "")))

    @get("/session", mcp_tool="session", sync_to_thread=False)
    def session_tool(request: "Request[Any, Any, Any]") -> "dict[str, str]":
        user = request.user
        return {"user": getattr(user, "sub", "")}

    middleware = [DefineMiddleware(MCPAuthBackend, token_validator=bearer_token_validator, user_resolver=resolve_user)]
    return Litestar(
        route_handlers=[session_tool],
        middleware=middleware,
        plugins=[LitestarMCP(MCPConfig(auth=build_mcp_auth_config()))],
    )


def _rpc_line(method: "str", params: "dict[str, Any] | None" = None, *, msg_id: "int | None" = 1) -> "bytes":
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        payload["id"] = msg_id
    if params is not None:
        payload["params"] = params
    return json.dumps(payload).encode() + b"\n"


def _parse_stdout(stdout: "BridgeBytesSink") -> "list[dict[str, Any]]":
    return [json.loads(line) for line in stdout.buffer.splitlines()]


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_calls_litestar_tool_through_custom_endpoint() -> "None":
    app = _build_app(base_path="/api/mcp")
    with _run_app(app) as base_url:
        stdout = BridgeBytesSink()
        stdin = BridgeQueuedBytesSource(
            _rpc_line("server/discover", msg_id=1),
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
    by_id = {message.get("id"): message for message in messages}
    assert exit_code == 0
    assert set(by_id) == {1, 2, 3}
    assert by_id[1]["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]
    assert any(tool["name"] == "hello" for tool in by_id[2]["result"]["tools"])
    content = by_id[3]["result"]["content"][0]["text"]
    assert json.loads(content) == {"message": "hello Ada"}


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_surfaces_auth_failure_as_stdio_error() -> "None":
    app = _build_auth_app()
    with _run_app(app) as base_url:
        stdout = BridgeBytesSink()
        stdin = BridgeQueuedBytesSource(_rpc_line("server/discover", msg_id=1))

        exit_code = await run_stdio_streamable_http_bridge(
            f"{base_url}/mcp",
            stdin=stdin,
            stdout=stdout,
            timeout=5,
        )

    messages = _parse_stdout(stdout)
    assert exit_code == 1
    assert messages[0]["error"]["code"] == -32001
    assert "401 Unauthorized" in messages[0]["error"]["message"]


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_stdout_contains_only_json_rpc_when_tool_logs() -> "None":
    logger = logging.getLogger("tests.bridge.stdout-purity")

    @get("/log", mcp_tool="log", sync_to_thread=False)
    def log_tool() -> "dict[str, str]":
        logger.warning("sentinel bridge log message")
        return {"ok": "true"}

    app = Litestar(route_handlers=[log_tool], plugins=[LitestarMCP(MCPConfig())])
    with _run_app(app) as base_url:
        stdout = BridgeBytesSink()
        stderr = io.StringIO()
        stdin = BridgeQueuedBytesSource(
            _rpc_line("server/discover", msg_id=1),
            _rpc_line("tools/call", {"name": "log", "arguments": {}}, msg_id=2),
        )

        exit_code = await run_stdio_streamable_http_bridge(
            f"{base_url}/mcp",
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            timeout=5,
        )

    assert exit_code == 0
    assert b"sentinel bridge log message" not in stdout.buffer
    messages = _parse_stdout(stdout)
    assert [message["jsonrpc"] for message in messages] == ["2.0", "2.0"]
    assert {message.get("id") for message in messages} == {1, 2}


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_mid_stream_server_death_exits_non_zero() -> "None":
    tool_started = threading.Event()

    @get("/slow", mcp_tool="slow", sync_to_thread=False)
    async def slow_tool() -> "dict[str, str]":
        tool_started.set()
        await sleep_forever()
        return {"ok": "true"}

    app = Litestar(route_handlers=[slow_tool], plugins=[LitestarMCP(MCPConfig())])
    with _run_app_server(app) as (base_url, server):
        stdout = BridgeBytesSink()
        stderr = io.StringIO()
        bridge_finished = anyio.Event()
        result: dict[str, int] = {}
        stdin = BridgeQueuedBytesSource(
            _rpc_line("server/discover", msg_id=1),
            _rpc_line("tools/call", {"name": "slow", "arguments": {}}, msg_id=2),
            block_after_chunks=True,
        )

        async def run_bridge() -> "None":
            try:
                result["exit_code"] = await run_stdio_streamable_http_bridge(
                    f"{base_url}/mcp",
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=5,
                )
            finally:
                bridge_finished.set()

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_bridge)
            with anyio.fail_after(5):
                assert await run_sync_in_worker_thread(tool_started.wait, 5)
            server.should_exit = True
            server.force_exit = True
            with anyio.fail_after(5):
                await bridge_finished.wait()
            task_group.cancel_scope.cancel()

    messages = _parse_stdout(stdout)
    assert result["exit_code"] == 1
    assert messages[-1]["error"]["code"] == -32001
    assert stderr.getvalue()


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_token_provider_refreshes_each_stateless_request() -> "None":
    app = _build_auth_app()
    token_count = 0

    def token_provider() -> "str":
        nonlocal token_count
        token_count += 1
        return mint_access_token(subject=f"integration-user-{token_count}")

    with _run_app(app) as base_url:
        stdout = BridgeBytesSink()
        stdin = BridgeQueuedBytesSource(
            _rpc_line("tools/call", {"name": "session", "arguments": {}}, msg_id=2),
            _rpc_line("tools/call", {"name": "session", "arguments": {}}, msg_id=3),
        )

        exit_code = await run_stdio_streamable_http_bridge(
            f"{base_url}/mcp",
            token_provider=token_provider,
            stdin=stdin,
            stdout=stdout,
            timeout=5,
        )

    messages = _parse_stdout(stdout)
    assert exit_code == 0
    assert {message.get("id") for message in messages} == {2, 3}
    users = {json.loads(message["result"]["content"][0]["text"])["user"] for message in messages}
    assert len(users) == 2
    assert token_count >= 3


@pytest.mark.anyio
@pytest.mark.integration
async def test_bridge_real_401_after_retry_exits_non_zero() -> "None":
    app = _build_auth_app()

    with _run_app(app) as base_url:
        stdout = BridgeBytesSink()
        stderr = io.StringIO()
        stdin = BridgeQueuedBytesSource(_rpc_line("server/discover", msg_id=1))

        exit_code = await run_stdio_streamable_http_bridge(
            f"{base_url}/mcp",
            token_provider=lambda: FORGED_TOKEN,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            timeout=5,
        )

    messages = _parse_stdout(stdout)
    assert exit_code == 1
    assert messages[0]["error"]["code"] == -32001
    assert "401" in messages[0]["error"]["message"]
