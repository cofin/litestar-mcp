"""Snippet: explicit binary and mixed MCP tool content blocks."""

from litestar import Litestar, Response, get

from litestar_mcp import LitestarMCP, MCPBlobResource, MCPResourceLink, MCPToolResult


def build() -> "Litestar":
    # start-example
    @get("/reports/latest", mcp_tool="generate_report")
    async def generate_report() -> "MCPToolResult":
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

    @get("/reports/inline", mcp_tool="download_report")
    async def download_report() -> "MCPBlobResource":
        payload = b"...pdf bytes..."
        return MCPBlobResource(
            uri="memory://reports/latest.pdf",
            data=payload,
            mime_type="application/pdf",
        )

    @get(
        "/reports/resource",
        mcp_resource="latest_report",
        mcp_resource_mime_type="application/pdf",
    )
    async def latest_report() -> "Response[bytes]":
        return Response(content=b"...pdf bytes...", media_type="application/pdf")

    app = Litestar(route_handlers=[generate_report, download_report, latest_report], plugins=[LitestarMCP()])
    # end-example
    return app
