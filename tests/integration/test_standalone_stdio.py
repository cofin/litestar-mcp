import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from litestar import Request, get
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException

import litestar_mcp
from litestar_mcp import MCP, MCPConfig
from litestar_mcp.utils import mcp_tool

pytestmark = pytest.mark.integration


class MockStream:
    def __init__(self, buffer: "Any") -> "None":
        self.buffer = buffer


def _stdio_context(**kwargs: "Any") -> "Any":
    return litestar_mcp.MCPStdioContext(**kwargs)


def _request(
    method: "str",
    *,
    request_id: "int" = 1,
    params: "dict[str, Any] | None" = None,
    tasks_capable: "bool" = False,
) -> "dict[str, Any]":
    request_params = dict(params or {})
    capabilities: dict[str, Any] = {}
    if tasks_capable:
        capabilities["extensions"] = {"io.modelcontextprotocol/tasks": {}}
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": capabilities,
        "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def _discover_request(request_id: int = 1) -> "dict[str, Any]":
    return _request("server/discover", request_id=request_id)


async def _run_stdio_exchange(
    mcp: "MCP",
    requests: "list[dict[str, Any]]",
    *,
    stdio_context: "Any | None" = None,
) -> "list[dict[str, Any]]":
    stdin_read_fd, stdin_write_fd = os.pipe()
    stdout_read_fd, stdout_write_fd = os.pipe()

    test_stdin_writer = os.fdopen(stdin_write_fd, "wb", buffering=0)
    test_stdout_reader = os.fdopen(stdout_read_fd, "rb", buffering=0)
    app_stdin = os.fdopen(stdin_read_fd, "rb", buffering=0)
    app_stdout = os.fdopen(stdout_write_fd, "wb", buffering=0)

    with (
        patch("sys.stdin", MockStream(app_stdin)),
        patch("sys.stdout", MockStream(app_stdout)),
    ):
        if stdio_context is None:
            run_task = asyncio.create_task(mcp._async_run_stdio())
        else:
            run_task = asyncio.create_task(mcp._async_run_stdio(stdio_context=stdio_context))

        try:
            loop = asyncio.get_running_loop()

            async def read_line_async() -> "bytes":
                return await asyncio.wait_for(
                    loop.run_in_executor(None, test_stdout_reader.readline),
                    timeout=5.0,
                )

            responses: list[dict[str, Any]] = []
            for request in requests:
                test_stdin_writer.write(json.dumps(request).encode("utf-8") + b"\n")
                if "id" in request:
                    responses.append(json.loads((await read_line_async()).decode("utf-8")))
            return responses
        finally:
            test_stdin_writer.close()
            await run_task
            test_stdout_reader.close()
            app_stdin.close()
            app_stdout.close()


async def _wait_for_terminal(store: "Any", task_id: "str", owner_id: "str | None") -> "Any":
    for _ in range(100):
        record = await store.get(task_id, owner_id)
        if record.is_terminal():
            return record
        await asyncio.sleep(0.01)
    msg = f"Task {task_id} did not reach a terminal state"
    raise AssertionError(msg)


def test_mcp_stdio_context_is_public_with_defaults() -> "None":
    context = _stdio_context()

    assert context.client_id == "stdio"
    assert context.owner_id is None
    assert context.user is None
    assert context.auth is None
    assert context.session is None
    assert context.state is None


@pytest.mark.anyio
async def test_standalone_stdio_tool_execution() -> "None":
    mcp = MCP(name="stdio-test")

    @mcp.tool(name="greet")
    def greet(name: "str") -> "str":
        return f"Hello {name}"

    # Setup OS pipes
    stdin_read_fd, stdin_write_fd = os.pipe()
    stdout_read_fd, stdout_write_fd = os.pipe()

    # Wrap write end of stdin and read end of stdout for test to use
    test_stdin_writer = os.fdopen(stdin_write_fd, "wb", buffering=0)
    test_stdout_reader = os.fdopen(stdout_read_fd, "rb", buffering=0)

    # Wrap read end of stdin and write end of stdout for app to use
    app_stdin = os.fdopen(stdin_read_fd, "rb", buffering=0)
    app_stdout = os.fdopen(stdout_write_fd, "wb", buffering=0)

    # Run _async_run_stdio in a background task
    with (
        patch("sys.stdin", MockStream(app_stdin)),
        patch("sys.stdout", MockStream(app_stdout)),
    ):
        run_task = asyncio.create_task(mcp._async_run_stdio())

        try:
            loop = asyncio.get_running_loop()

            async def read_line_async() -> "bytes":
                # Timeout after 5s to prevent hanging tests
                return await asyncio.wait_for(
                    loop.run_in_executor(None, test_stdout_reader.readline),
                    timeout=5.0,
                )

            # 1. Discover the server.
            test_stdin_writer.write(json.dumps(_discover_request()).encode("utf-8") + b"\n")

            discovery_resp = json.loads((await read_line_async()).decode("utf-8"))
            assert discovery_resp["id"] == 1
            assert "result" in discovery_resp

            # 2. Call a tool with its own complete request metadata.
            tool_req = _request(
                "tools/call",
                request_id=2,
                params={"name": "greet", "arguments": {"name": "World"}},
            )
            test_stdin_writer.write(json.dumps(tool_req).encode("utf-8") + b"\n")

            tool_resp_bytes = await read_line_async()
            tool_resp = json.loads(tool_resp_bytes.decode("utf-8"))
            assert tool_resp["id"] == 2
            assert "result" in tool_resp
            assert tool_resp["result"]["content"][0]["text"] == "Hello World"

        finally:
            # Clean up: close test writer to trigger EOF in runner stdio loop
            test_stdin_writer.close()
            # Wait for runner task to finish
            await run_task
            test_stdout_reader.close()
            app_stdin.close()
            app_stdout.close()


@pytest.mark.anyio
async def test_standalone_stdio_context_propagates_identity_to_tool() -> "None":
    user = SimpleNamespace(id="user-123")
    session = {"tenant": "acme"}
    state = {"feature": "enabled"}

    @get("/whoami", mcp_tool="whoami", sync_to_thread=False)
    def whoami(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        session_scope = cast("dict[str, Any]", request.scope["session"])
        state_scope = request.scope["state"]
        auth_scope = cast("dict[str, Any]", request.scope["auth"])
        session_scope["mutated"] = "yes"
        state_scope["mutated"] = "yes"
        return {
            "user_id": request.user.id,
            "auth_sub": auth_scope["sub"],
            "tenant": session_scope["tenant"],
            "feature": state_scope["feature"],
        }

    mcp = MCP(name="stdio-context-test", route_handlers=[whoami])
    context = _stdio_context(
        user=user,
        auth={"sub": "auth-subject", "role": "admin"},
        session=session,
        state=state,
    )

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "whoami", "arguments": {}}),
        ],
        stdio_context=context,
    )

    payload = json.loads(responses[1]["result"]["content"][0]["text"])
    assert payload == {
        "user_id": "user-123",
        "auth_sub": "auth-subject",
        "tenant": "acme",
        "feature": "enabled",
    }
    assert session == {"tenant": "acme"}
    assert state == {"feature": "enabled"}


@pytest.mark.anyio
async def test_standalone_stdio_context_isolates_auth_mutations_between_calls() -> "None":
    auth = {"sub": "auth-subject"}

    @get("/probe", mcp_tool="probe", sync_to_thread=False)
    def probe(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        auth_scope = cast("dict[str, Any]", request.scope["auth"])
        seen_mutation = auth_scope.get("mutation")
        auth_scope["mutation"] = "leaked"
        return {"seen_mutation": seen_mutation}

    mcp = MCP(name="stdio-auth-isolation-test", route_handlers=[probe])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "probe", "arguments": {}}),
            _request("tools/call", request_id=3, params={"name": "probe", "arguments": {}}),
        ],
        stdio_context=_stdio_context(auth=auth),
    )

    first = json.loads(responses[1]["result"]["content"][0]["text"])
    second = json.loads(responses[2]["result"]["content"][0]["text"])
    assert first["seen_mutation"] is None
    # A per-call copy means the first call's write must not leak into the second.
    assert second["seen_mutation"] is None
    # The caller's original auth dict is likewise untouched.
    assert auth == {"sub": "auth-subject"}


@pytest.mark.anyio
async def test_standalone_stdio_context_authorizes_guards_from_scope() -> "None":
    def require_admin(connection: "Any", _handler: "Any") -> "None":
        auth = connection.scope.get("auth") or {}
        if auth.get("role") != "admin":
            msg = "admin role required"
            raise NotAuthorizedException(msg)

    @get("/guarded", guards=[require_admin], mcp_tool="guarded", sync_to_thread=False)
    def guarded() -> "dict[str, bool]":
        return {"ok": True}

    mcp = MCP(name="stdio-guard-test", route_handlers=[guarded])

    allowed = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "guarded", "arguments": {}}),
        ],
        stdio_context=_stdio_context(auth={"role": "admin"}),
    )
    assert allowed[1]["result"]["isError"] is False

    denied = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("tools/call", request_id=2, params={"name": "guarded", "arguments": {}}),
        ],
        stdio_context=_stdio_context(auth={"role": "viewer"}),
    )
    assert denied[1]["result"]["isError"] is True


@pytest.mark.anyio
async def test_standalone_stdio_context_propagates_identity_to_resources() -> "None":
    @get("/profile", mcp_resource="profile", sync_to_thread=False)
    def profile(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        return {
            "user_id": request.user.id,
            "auth_sub": request.scope["auth"]["sub"],
        }

    mcp = MCP(name="stdio-resource-test", route_handlers=[profile])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request("resources/read", request_id=2, params={"uri": "litestar://profile"}),
        ],
        stdio_context=_stdio_context(user=SimpleNamespace(id="resource-user"), auth={"sub": "resource-sub"}),
    )

    payload = json.loads(responses[1]["result"]["contents"][0]["text"])
    assert payload == {"user_id": "resource-user", "auth_sub": "resource-sub"}


@pytest.mark.anyio
async def test_standalone_stdio_task_owner_defaults_to_auth_subject() -> "None":
    @get("/optional-task", sync_to_thread=False)
    @mcp_tool(name="owner_task", task_support="optional")
    async def owner_task(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
        return {"auth_sub": request.scope["auth"]["sub"]}

    mcp = MCP(name="stdio-task-owner-test", config=MCPConfig(tasks=True), route_handlers=[owner_task])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request(
                "tools/call",
                request_id=2,
                params={"name": "owner_task", "arguments": {}},
                tasks_capable=True,
            ),
        ],
        stdio_context=_stdio_context(auth={"sub": "owner-from-auth"}),
    )

    assert responses[1]["result"]["resultType"] == "task"
    task_id = responses[1]["result"]["taskId"]
    assert mcp.plugin.task_store is not None
    record = await _wait_for_terminal(mcp.plugin.task_store, task_id, "owner-from-auth")
    assert record.owner_id == "owner-from-auth"
    assert record.result is not None
    payload = json.loads(record.result["content"][0]["text"])
    assert payload == {"auth_sub": "owner-from-auth"}


@pytest.mark.anyio
async def test_standalone_stdio_task_owner_prefers_explicit_owner_id() -> "None":
    @get("/optional-task", sync_to_thread=False)
    @mcp_tool(name="explicit_owner_task", task_support="optional")
    async def explicit_owner_task() -> "dict[str, bool]":
        return {"ok": True}

    mcp = MCP(name="stdio-explicit-owner-test", config=MCPConfig(tasks=True), route_handlers=[explicit_owner_task])

    responses = await _run_stdio_exchange(
        mcp,
        [
            _discover_request(),
            _request(
                "tools/call",
                request_id=2,
                params={"name": "explicit_owner_task", "arguments": {}},
                tasks_capable=True,
            ),
        ],
        stdio_context=_stdio_context(owner_id="explicit-owner", auth={"sub": "auth-owner"}),
    )

    assert responses[1]["result"]["resultType"] == "task"
    task_id = responses[1]["result"]["taskId"]
    assert mcp.plugin.task_store is not None
    record = await mcp.plugin.task_store.get(task_id, "explicit-owner")
    assert record.owner_id == "explicit-owner"


@pytest.mark.anyio
async def test_standalone_stdio_litestar_dependency_resolution() -> "None":
    def provide_suffix() -> "str":
        return "!"

    @get(
        "/greet",
        mcp_tool="greet",
        dependencies={"suffix": Provide(provide_suffix, sync_to_thread=False)},
    )
    async def greet(name: "str", suffix: "str") -> "dict[str, str]":
        return {"message": f"Hello {name}{suffix}"}

    mcp = MCP(name="stdio-di-test", route_handlers=[greet])

    stdin_read_fd, stdin_write_fd = os.pipe()
    stdout_read_fd, stdout_write_fd = os.pipe()

    test_stdin_writer = os.fdopen(stdin_write_fd, "wb", buffering=0)
    test_stdout_reader = os.fdopen(stdout_read_fd, "rb", buffering=0)
    app_stdin = os.fdopen(stdin_read_fd, "rb", buffering=0)
    app_stdout = os.fdopen(stdout_write_fd, "wb", buffering=0)

    with (
        patch("sys.stdin", MockStream(app_stdin)),
        patch("sys.stdout", MockStream(app_stdout)),
    ):
        run_task = asyncio.create_task(mcp._async_run_stdio())

        try:
            loop = asyncio.get_running_loop()

            async def read_line_async() -> "bytes":
                return await asyncio.wait_for(
                    loop.run_in_executor(None, test_stdout_reader.readline),
                    timeout=5.0,
                )

            test_stdin_writer.write(json.dumps(_discover_request()).encode("utf-8") + b"\n")
            discovery_resp = json.loads((await read_line_async()).decode("utf-8"))
            assert "result" in discovery_resp

            test_stdin_writer.write(
                json.dumps(
                    _request(
                        "tools/call",
                        request_id=2,
                        params={"name": "greet", "arguments": {"name": "World"}},
                    )
                ).encode("utf-8")
                + b"\n"
            )

            tool_resp = json.loads((await read_line_async()).decode("utf-8"))
            assert tool_resp["id"] == 2
            assert json.loads(tool_resp["result"]["content"][0]["text"]) == {"message": "Hello World!"}
        finally:
            test_stdin_writer.close()
            await run_task
            test_stdout_reader.close()
            app_stdin.close()
            app_stdout.close()


@pytest.mark.anyio
async def test_standalone_stdio_lifespan_hooks() -> "None":
    startup_called = False
    shutdown_called = False

    async def on_startup() -> "None":
        nonlocal startup_called
        startup_called = True

    async def on_shutdown() -> "None":
        nonlocal shutdown_called
        shutdown_called = True

    mcp = MCP(
        name="lifespan-test",
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
    )

    # Setup OS pipes
    stdin_read_fd, stdin_write_fd = os.pipe()
    stdout_read_fd, stdout_write_fd = os.pipe()

    test_stdin_writer = os.fdopen(stdin_write_fd, "wb", buffering=0)
    test_stdout_reader = os.fdopen(stdout_read_fd, "rb", buffering=0)
    app_stdin = os.fdopen(stdin_read_fd, "rb", buffering=0)
    app_stdout = os.fdopen(stdout_write_fd, "wb", buffering=0)

    with (
        patch("sys.stdin", MockStream(app_stdin)),
        patch("sys.stdout", MockStream(app_stdout)),
    ):
        run_task = asyncio.create_task(mcp._async_run_stdio())

        try:
            # Wait a short moment to let startup run
            await asyncio.sleep(0.5)
            assert startup_called is True
            assert shutdown_called is False
        finally:
            # Close stdin to trigger shutdown
            test_stdin_writer.close()
            await run_task
            assert shutdown_called is True

            test_stdout_reader.close()
            app_stdin.close()
            app_stdout.close()
