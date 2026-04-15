"""Tests for the typed MCPDependencyProvider Protocol and ToolExecutionContext.request."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.decorators import mcp_tool
from litestar_mcp.executor import ToolExecutionContext, execute_tool
from litestar_mcp.types import MCPDependencyProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def test_protocol_is_runtime_checkable() -> None:
    """A valid dependency provider structurally matches the Protocol."""

    @asynccontextmanager
    async def valid_provider(ctx: ToolExecutionContext) -> "AsyncIterator[dict[str, Any]]":
        yield {}

    # runtime_checkable Protocol: isinstance should succeed for a plain callable
    # matching the signature shape, since Protocol membership is structural on
    # callability.
    assert isinstance(valid_provider, MCPDependencyProvider)


def test_tool_execution_context_defaults_request_to_none() -> None:
    """Backwards-compatible default: CLI/stdio callers omit request."""
    from litestar import Litestar as _Litestar

    ctx = ToolExecutionContext(
        app=_Litestar(route_handlers=[]),
        handler=None,  # type: ignore[arg-type]
        tool_args={},
    )
    assert ctx.request is None


@pytest.mark.asyncio
async def test_request_is_populated_for_http_invocation() -> None:
    """When a tool is invoked over HTTP, the provider sees the Request."""
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def capture_request(ctx: ToolExecutionContext) -> "AsyncIterator[dict[str, Any]]":
        captured["request"] = ctx.request
        captured["has_headers"] = ctx.request is not None and "user-agent" in ctx.request.headers
        yield {}

    @get("/echo", sync_to_thread=False)
    @mcp_tool(name="echo")
    def echo_tool() -> dict[str, str]:
        """Echo."""
        return {"ok": "yes"}

    app = Litestar(
        route_handlers=[echo_tool],
        plugins=[LitestarMCP(MCPConfig(dependency_provider=capture_request))],
    )

    with TestClient(app=app) as client:
        init = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {}},
            headers={"User-Agent": "pytest"},
        )
        assert init.status_code == 200
        session_id = init.headers.get("mcp-session-id")
        assert session_id, "expected session header on initialize response"

        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers={"mcp-session-id": session_id},
        )

        call = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {}},
            },
            headers={"User-Agent": "pytest", "mcp-session-id": session_id},
        )
        assert call.status_code == 200

    assert captured.get("request") is not None, "request must be forwarded to dependency_provider on HTTP"
    assert captured.get("has_headers") is True


@pytest.mark.asyncio
async def test_request_is_none_on_cli_invocation() -> None:
    """CLI/stdio tool execution must leave ctx.request as None."""
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def probe(ctx: ToolExecutionContext) -> "AsyncIterator[dict[str, Any]]":
        captured["request"] = ctx.request
        yield {}

    @get("/cli-tool", sync_to_thread=False)
    @mcp_tool(name="cli_tool")
    def cli_tool() -> dict[str, str]:
        """A CLI tool."""
        return {"ok": "yes"}

    app = Litestar(
        route_handlers=[cli_tool],
        plugins=[LitestarMCP(MCPConfig(dependency_provider=probe))],
    )
    # Direct execution mirrors what the CLI does.
    await execute_tool(cli_tool, app, {}, config=app.plugins.get(LitestarMCP).config)

    assert captured["request"] is None
