"""Snippet: marking a route as an MCP resource. Referenced from docs/usage/marking.rst."""

from typing import Any

from litestar import Litestar, get

from litestar_mcp import LitestarMCP


def build() -> Litestar:
    # start-example
    @get("/api/schema", mcp_resource="api_schema")
    async def get_schema() -> dict[str, Any]:
        """Return the API JSON schema - exposed as the ``api_schema`` MCP resource."""
        return {"type": "object", "properties": {}}

    app = Litestar(route_handlers=[get_schema], plugins=[LitestarMCP()])
    # end-example
    return app
