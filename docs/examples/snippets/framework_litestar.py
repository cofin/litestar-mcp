"""Snippet: framework-level Litestar registration. Referenced from docs/usage/framework.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig


def build() -> Litestar:
    # start-example
    app = Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(MCPConfig(base_path="/mcp"))],
    )
    # end-example
    return app
