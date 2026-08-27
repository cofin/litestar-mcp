"""Tests for public MCP content helper objects."""

import base64

import pytest

from litestar_mcp import MCPBlobResource, MCPResourceLink, MCPToolResult


def test_resource_link_normalizes_to_content_block() -> "None":
    link = MCPResourceLink(
        name="report.pdf",
        uri="litestar://reports/latest",
        mime_type="application/pdf",
        description="Latest report",
        title="Latest Report",
        size=128,
        annotations={"audience": ["user"]},
        meta={"trace": "abc"},
    )

    assert link.to_content_block() == {
        "type": "resource_link",
        "name": "report.pdf",
        "uri": "litestar://reports/latest",
        "mimeType": "application/pdf",
        "description": "Latest report",
        "title": "Latest Report",
        "size": 128,
        "annotations": {"audience": ["user"]},
        "_meta": {"trace": "abc"},
    }


def test_blob_resource_normalizes_to_embedded_resource_block() -> "None":
    payload = b"\x00\x01report"
    blob = MCPBlobResource(
        uri="memory://reports/latest.pdf",
        data=payload,
        mime_type="application/pdf",
        annotations={"priority": 0.8},
        meta={"trace": "abc"},
    )

    assert blob.to_content_block(max_blob_bytes=len(payload)) == {
        "type": "resource",
        "resource": {
            "uri": "memory://reports/latest.pdf",
            "mimeType": "application/pdf",
            "blob": base64.b64encode(payload).decode("ascii"),
            "annotations": {"priority": 0.8},
            "_meta": {"trace": "abc"},
        },
    }


def test_blob_resource_enforces_max_blob_bytes_before_encoding() -> "None":
    blob = MCPBlobResource(uri="memory://too-large", data=b"1234")

    with pytest.raises(ValueError, match="max_blob_bytes"):
        blob.to_content_block(max_blob_bytes=3)


def test_tool_result_normalizes_mixed_content_and_structured_content() -> "None":
    result = MCPToolResult(
        content=[
            {"type": "text", "text": "generated"},
            MCPResourceLink(name="report.pdf", uri="litestar://reports/latest", mime_type="application/pdf"),
        ],
        structured_content={"reportId": "latest"},
        meta={"trace": "abc"},
    )

    assert result.to_result() == {
        "content": [
            {"type": "text", "text": "generated"},
            {
                "type": "resource_link",
                "name": "report.pdf",
                "uri": "litestar://reports/latest",
                "mimeType": "application/pdf",
            },
        ],
        "isError": False,
        "structuredContent": {"reportId": "latest"},
        "_meta": {"trace": "abc"},
    }
