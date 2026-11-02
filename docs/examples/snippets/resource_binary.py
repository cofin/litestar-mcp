"""Snippet: binary MCP resources with MIME metadata."""

from litestar import Litestar, get

from litestar_mcp import LitestarMCP, mcp_resource


def build() -> "Litestar":
    # start-route-example
    @get("/reports/latest", mcp_resource=True, media_type="application/pdf")
    async def latest_report() -> bytes:
        return b"...pdf bytes..."

    # end-route-example

    # start-decorator-example
    @mcp_resource(mime_type="application/pdf")
    @get("/reports/archive")
    async def archived_report() -> bytes:
        return b"...pdf bytes..."

    # end-decorator-example

    return Litestar(route_handlers=[latest_report, archived_report], plugins=[LitestarMCP()])
