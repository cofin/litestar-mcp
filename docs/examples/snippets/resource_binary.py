"""Snippet: binary MCP resources with MIME metadata."""

from litestar import Litestar, Response, get

from litestar_mcp import LitestarMCP, mcp_resource


def build() -> "Litestar":
    # start-route-example
    @get(
        "/reports/latest",
        mcp_resource="latest_report",
        mcp_resource_mime_type="application/pdf",
    )
    async def latest_report() -> "Response[bytes]":
        return Response(content=b"...pdf bytes...", media_type="application/pdf")

    # end-route-example

    # start-decorator-example
    @mcp_resource("archived_report", mime_type="application/pdf")
    @get("/reports/archive")
    async def archived_report() -> "Response[bytes]":
        return Response(content=b"...pdf bytes...", media_type="application/pdf")

    # end-decorator-example

    app = Litestar(route_handlers=[latest_report, archived_report], plugins=[LitestarMCP()])
    return app
