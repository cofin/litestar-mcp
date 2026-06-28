import asyncio
import json
import os
from typing import Any
from unittest.mock import patch

import pytest
from litestar import get
from litestar.di import Provide

from litestar_mcp import MCP

pytestmark = pytest.mark.integration


class MockStream:
    def __init__(self, buffer: "Any") -> "None":
        self.buffer = buffer


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

            # 1. Send initialize request
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            }
            test_stdin_writer.write(json.dumps(init_req).encode("utf-8") + b"\n")

            init_resp_bytes = await read_line_async()
            init_resp = json.loads(init_resp_bytes.decode("utf-8"))
            assert init_resp["id"] == 1
            assert "result" in init_resp

            # 2. Send initialized notification
            initialized_ntf = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            test_stdin_writer.write(json.dumps(initialized_ntf).encode("utf-8") + b"\n")

            # 3. Call tool
            tool_req = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "greet", "arguments": {"name": "World"}},
            }
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

            test_stdin_writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "test-client", "version": "1.0"},
                        },
                    }
                ).encode("utf-8")
                + b"\n"
            )
            init_resp = json.loads((await read_line_async()).decode("utf-8"))
            assert "result" in init_resp

            test_stdin_writer.write(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8") + b"\n"
            )

            test_stdin_writer.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "greet", "arguments": {"name": "World"}},
                    }
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
