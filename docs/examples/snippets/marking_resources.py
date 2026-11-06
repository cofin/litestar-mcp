"""Snippet: marking a route as an MCP resource."""

# --8<-- [start:example]
from typing import Any

from litestar import Litestar, get

from litestar_mcp import LitestarMCP


@get("/api/schema", mcp_resource="api_schema")
async def get_schema() -> dict[str, Any]:
    """Return the API JSON schema - exposed as the ``api_schema`` MCP resource."""
    return {"type": "object", "properties": {}}


app = Litestar(route_handlers=[get_schema], plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
