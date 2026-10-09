"""Unit tests for MCPStreamableHTTPClient and request helpers."""

from typing import Any, cast

import httpx2
import pytest
from litestar import Litestar

from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.client import (
    MCPClientError,
    MCPStreamableHTTPClient,
    mcp_request_headers,
    prepare_mcp_request,
)
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.routes import MCP_PROTOCOL_VERSION


def test_prepare_mcp_request_adds_meta() -> None:
    """prepare_mcp_request adds client meta information to request params."""
    msg = {"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
    prepared = prepare_mcp_request(msg, client_info={"name": "test-client", "version": "1.0"})
    assert "params" in prepared
    meta = prepared["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == MCP_PROTOCOL_VERSION
    assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "test-client"


def test_mcp_request_headers_generation() -> None:
    """mcp_request_headers sets standard headers and encodes tool name."""
    msg = {"jsonrpc": "2.0", "id": "2", "method": "tools/call", "params": {"name": "calculate"}}
    headers = httpx2.Headers(mcp_request_headers(msg))
    assert headers["Accept"] == "application/json, text/event-stream"
    assert headers["Content-Type"] == "application/json"
    assert headers["mcp-protocol-version"] == MCP_PROTOCOL_VERSION
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "calculate"


@tool(description="Multiply two numbers.")
def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the product."""
    return a * b


@pytest.mark.anyio
async def test_mcp_client_against_litestar_server() -> None:
    """MCPStreamableHTTPClient lists and executes tools on a LitestarMCP server."""
    plugin = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    plugin.register_tool(multiply)
    app = Litestar(plugins=[plugin])

    transport = httpx2.ASGITransport(app=cast("Any", app))
    client = MCPStreamableHTTPClient(
        endpoint="http://testserver/mcp",
        transport=transport,
    )

    tools = await client.list_tools()
    assert len(tools) >= 1
    tool_names = [t["name"] for t in tools]
    assert "multiply" in tool_names

    result = await client.call_tool("multiply", {"a": 6, "b": 7})
    assert result in (42, "42")


@pytest.mark.anyio
async def test_mcp_client_error_handling() -> None:
    """MCPStreamableHTTPClient raises MCPClientError on unknown tool or error."""
    plugin = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    app = Litestar(plugins=[plugin])

    transport = httpx2.ASGITransport(app=cast("Any", app))
    client = MCPStreamableHTTPClient(
        endpoint="http://testserver/mcp",
        transport=transport,
    )

    with pytest.raises(MCPClientError):
        await client.call_tool("nonexistent_tool", {})
