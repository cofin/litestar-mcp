"""Snippet: custom MCPConfig. Referenced from docs/usage/configuration.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig


def build() -> Litestar:
    # start-example
    config = MCPConfig(
        base_path="/api/mcp",
        name="My MCP Server",
        include_in_schema=True,
    )
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
    # end-example
    return app
