"""Unit tests for MCPStreamableHTTPClient and request helpers."""

from typing import Any, cast

import httpx2
import pytest
from litestar import Litestar, get

from litestar_mcp.core.serialization import from_json
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.client import (
    MCPClientError,
    MCPStreamableHTTPClient,
    _encode_header_value,
    mcp_request_headers,
    prepare_mcp_request,
)
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.routes import MCP_PROTOCOL_VERSION, decode_mcp_header_value


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


@pytest.mark.parametrize(
    ("raw_value", "expected_encoded"),
    [
        ("calculate", "calculate"),
        ("données_créer", "=?base64?ZG9ubsOpZXNfY3LDqWVy?="),
        ("=?base64?foo?=", "=?base64?PT9iYXNlNjQ/Zm9vPz0=?="),
        ("", "=?base64??="),
        ("bad\x7fname", "=?base64?YmFkf25hbWU=?="),
    ],
)
def test_encode_header_value_round_trips_with_decode_mcp_header_value(
    raw_value: str,
    expected_encoded: str,
) -> None:
    """_encode_header_value uses the =?base64?...?= sentinel and round-trips through decode_mcp_header_value."""
    encoded = _encode_header_value(raw_value)
    assert encoded == expected_encoded
    assert decode_mcp_header_value(encoded) == raw_value


@pytest.mark.anyio
async def test_streamable_http_client_encodes_non_ascii_mcp_name_header_with_base64_sentinel() -> None:
    """MCPStreamableHTTPClient encodes non-ASCII Mcp-Name headers with =?base64?...?= accepted by MCPController."""

    @get("/unicode-tool", mcp_tool="données_créer", sync_to_thread=False)
    def unicode_tool() -> dict[str, str]:
        return {"status": "créé"}

    app = Litestar(route_handlers=[unicode_tool], plugins=[LitestarMCP()])
    headers = mcp_request_headers({"method": "tools/call", "params": {"name": "données_créer"}})
    assert headers["Mcp-Name"] == "=?base64?ZG9ubsOpZXNfY3LDqWVy?="
    assert decode_mcp_header_value(headers["Mcp-Name"]) == "données_créer"

    transport = httpx2.ASGITransport(app=cast("Any", app))
    async with MCPStreamableHTTPClient("http://testserver/mcp", transport=transport) as client:
        result = await client.call_tool("données_créer", {})
    assert from_json(result) == {"status": "créé"}
