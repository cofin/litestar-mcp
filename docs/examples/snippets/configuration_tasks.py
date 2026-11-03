"""Snippet: enabling the MCP Tasks extension."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig


def build() -> "Litestar":
    # start-example
    app = Litestar(plugins=[LitestarMCP(MCPConfig(tasks=True))])
    # end-example
    return app
