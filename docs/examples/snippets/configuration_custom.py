"""Snippet: custom MCPConfig."""

# --8<-- [start:example]
from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig

config = MCPConfig(
    base_path="/api/mcp",
    name="My MCP Server",
    include_in_schema=True,
)
app = Litestar(plugins=[LitestarMCP(config)])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
