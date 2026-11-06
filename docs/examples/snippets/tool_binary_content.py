"""Snippet: explicit binary and mixed MCP tool content blocks."""

# --8<-- [start:example]
from litestar import Litestar, get

from litestar_mcp import LitestarMCP, MCPBlobResource, MCPResourceLink, MCPToolResult


@get("/reports/latest", mcp_tool=True)
async def generate_report() -> MCPToolResult:
    return MCPToolResult(
        content=[
            {"type": "text", "text": "Report generated."},
            MCPResourceLink(
                name="report.pdf",
                uri="litestar://latest_report",
                mime_type="application/pdf",
            ),
        ],
        structured_content={"reportId": "latest"},
    )


@get("/reports/inline", mcp_tool=True)
async def download_report() -> MCPBlobResource:
    payload = b"...pdf bytes..."
    return MCPBlobResource(
        uri="memory://reports/latest.pdf",
        data=payload,
        mime_type="application/pdf",
    )


@get("/reports/resource", mcp_resource=True, media_type="application/pdf")
async def latest_report() -> bytes:
    return b"...pdf bytes..."


app = Litestar(route_handlers=[generate_report, download_report, latest_report], plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
