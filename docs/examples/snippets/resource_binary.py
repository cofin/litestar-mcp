"""Snippet: binary MCP resources with MIME metadata."""

# --8<-- [start:example]
from litestar import Litestar, get

from litestar_mcp import LitestarMCP, mcp_resource


@get("/reports/latest", mcp_resource=True, media_type="application/pdf")
async def latest_report() -> bytes:
    return b"...pdf bytes..."


@mcp_resource(mime_type="application/pdf")
@get("/reports/archive")
async def archived_report() -> bytes:
    return b"...pdf bytes..."


app = Litestar(route_handlers=[latest_report, archived_report], plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
