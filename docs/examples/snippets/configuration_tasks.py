"""Snippet: enabling the MCP Tasks extension."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.config import MCPTaskConfig


def build() -> "Litestar":
    # start-example
    config = MCPConfig(
        tasks=MCPTaskConfig(
            default_ttl_ms=300_000,
            max_ttl_ms=3_600_000,
            poll_interval_ms=1_000,
        ),
    )
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
    # end-example
    return app
