"""Snippet: enabling the MCP Tasks extension."""

# --8<-- [start:example]
from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig

app = Litestar(plugins=[LitestarMCP(MCPConfig(tasks=True))])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
