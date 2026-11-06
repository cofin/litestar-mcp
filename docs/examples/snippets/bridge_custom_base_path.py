"""Snippet: bridge target app with a custom MCP base path."""

# --8<-- [start:example]
from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig

app = Litestar(
    plugins=[LitestarMCP(MCPConfig(base_path="/api/mcp"))],
)
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
