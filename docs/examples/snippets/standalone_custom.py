"""Snippet: passing Litestar plugins and kwargs to standalone MCP."""

# --8<-- [start:example]
from litestar_mcp import MCP


class DummyPlugin:
    """Example Litestar plugin forwarded through standalone MCP."""


plugin = DummyPlugin()

mcp = MCP(
    name="my-mcp-server",
    plugins=[plugin],
)
# --8<-- [end:example]
