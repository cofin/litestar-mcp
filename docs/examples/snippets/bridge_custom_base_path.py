"""Snippet: bridge target app with a custom MCP base path."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig


def build() -> "Litestar":
    # start-example
    app = Litestar(
        plugins=[LitestarMCP(MCPConfig(base_path="/api/mcp"))],
    )
    # end-example
    return app
