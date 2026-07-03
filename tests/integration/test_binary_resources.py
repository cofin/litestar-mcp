"""Binary artifact flows over MCP 2025-11-25 content blocks."""

import base64
from typing import Any

import pytest
from litestar import Litestar, Response, get
from litestar.testing import AsyncTestClient

from litestar_mcp import LitestarMCP, MCPBlobResource, MCPConfig, MCPResourceLink, MCPToolResult

pytestmark = pytest.mark.integration


@pytest.fixture
def anyio_backend() -> "str":
    return "asyncio"


def _app(*handlers: "Any", config: "MCPConfig | None" = None) -> "Litestar":
    return Litestar(route_handlers=list(handlers), plugins=[LitestarMCP(config or MCPConfig())])


async def _init(client: "AsyncTestClient[Any]") -> "str":
    init = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return str(sid)


async def _rpc(
    client: "AsyncTestClient[Any]", method: "str", params: "dict[str, Any] | None" = None, *, sid: "str"
) -> "dict[str, Any]":
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return (await client.post("/mcp", json=body, headers={"Mcp-Session-Id": sid})).json()  # type: ignore[no-any-return]


@pytest.mark.anyio
async def test_tool_can_return_embedded_blob_resource() -> "None":
    payload = b"%PDF"

    @get("/download", mcp_tool="download_url", sync_to_thread=False)
    def download_url() -> "MCPBlobResource":
        return MCPBlobResource(uri="memory://downloads/report.pdf", data=payload, mime_type="application/pdf")

    async with AsyncTestClient(app=_app(download_url)) as client:
        sid = await _init(client)
        resp = await _rpc(client, "tools/call", {"name": "download_url", "arguments": {}}, sid=sid)

    assert resp["result"]["isError"] is False
    assert resp["result"]["content"] == [
        {
            "type": "resource",
            "resource": {
                "uri": "memory://downloads/report.pdf",
                "mimeType": "application/pdf",
                "blob": base64.b64encode(payload).decode("ascii"),
            },
        }
    ]


@pytest.mark.anyio
async def test_tool_resource_link_can_be_read_as_blob_resource() -> "None":
    payload = b"\x00\x01report"

    @get("/generate-report", mcp_tool="generate_report", sync_to_thread=False)
    def generate_report() -> "MCPResourceLink":
        return MCPResourceLink(
            name="report.pdf",
            uri="litestar://report",
            mime_type="application/pdf",
            size=len(payload),
        )

    @get("/report", mcp_resource="report", mcp_resource_mime_type="application/pdf", sync_to_thread=False)
    def report() -> "Response[bytes]":
        return Response(content=payload, media_type="application/pdf")

    async with AsyncTestClient(app=_app(generate_report, report)) as client:
        sid = await _init(client)
        listed = await _rpc(client, "resources/list", sid=sid)
        tool_resp = await _rpc(client, "tools/call", {"name": "generate_report", "arguments": {}}, sid=sid)
        read_resp = await _rpc(client, "resources/read", {"uri": "litestar://report"}, sid=sid)

    resource = next(item for item in listed["result"]["resources"] if item["name"] == "report")
    assert resource["mimeType"] == "application/pdf"
    assert tool_resp["result"]["content"] == [
        {
            "type": "resource_link",
            "name": "report.pdf",
            "uri": "litestar://report",
            "mimeType": "application/pdf",
            "size": len(payload),
        }
    ]
    assert read_resp["result"]["contents"] == [
        {
            "uri": "litestar://report",
            "mimeType": "application/pdf",
            "blob": base64.b64encode(payload).decode("ascii"),
        }
    ]


@pytest.mark.anyio
async def test_tool_result_preserves_structured_content_and_content_blocks() -> "None":
    @get("/mixed", mcp_tool="mixed", sync_to_thread=False)
    def mixed() -> "MCPToolResult":
        return MCPToolResult(
            content=[
                {"type": "text", "text": "generated"},
                MCPResourceLink(name="report.pdf", uri="litestar://report", mime_type="application/pdf"),
            ],
            structured_content={"reportId": "report"},
            meta={"trace": "abc"},
        )

    async with AsyncTestClient(app=_app(mixed)) as client:
        sid = await _init(client)
        resp = await _rpc(client, "tools/call", {"name": "mixed", "arguments": {}}, sid=sid)

    assert resp["result"]["content"] == [
        {"type": "text", "text": "generated"},
        {
            "type": "resource_link",
            "name": "report.pdf",
            "uri": "litestar://report",
            "mimeType": "application/pdf",
        },
    ]
    assert resp["result"]["structuredContent"] == {"reportId": "report"}
    assert resp["result"]["_meta"] == {"trace": "abc"}


@pytest.mark.anyio
async def test_blob_size_guard_applies_to_tools_and_resources_read() -> "None":
    payload = b"1234"

    @get("/too-large-tool", mcp_tool="too_large_tool", sync_to_thread=False)
    def too_large_tool() -> "MCPBlobResource":
        return MCPBlobResource(uri="memory://too-large", data=payload)

    @get("/too-large-resource", mcp_resource="too_large_resource", sync_to_thread=False)
    def too_large_resource() -> "Response[bytes]":
        return Response(content=payload, media_type="application/octet-stream")

    async with AsyncTestClient(
        app=_app(too_large_tool, too_large_resource, config=MCPConfig(max_blob_bytes=3))
    ) as client:
        sid = await _init(client)
        tool_resp = await _rpc(client, "tools/call", {"name": "too_large_tool", "arguments": {}}, sid=sid)
        resource_resp = await _rpc(client, "resources/read", {"uri": "litestar://too_large_resource"}, sid=sid)

    assert tool_resp["result"]["isError"] is True
    assert "max_blob_bytes" in tool_resp["result"]["content"][0]["text"]
    assert resource_resp["error"]["message"] == "Resource read failed"
    assert "max_blob_bytes" in resource_resp["error"]["data"]["detail"]
