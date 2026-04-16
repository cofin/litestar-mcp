"""Snippet: enabling MCP task support. Referenced from docs/usage/tasks.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.config import MCPTaskConfig


def build() -> Litestar:
    # start-example
    config = MCPConfig(
        tasks=MCPTaskConfig(enabled=True, default_ttl=300_000),
    )
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
    # end-example
    return app
