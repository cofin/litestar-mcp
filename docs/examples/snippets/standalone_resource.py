"""Snippet: registering a resource on a standalone MCP server."""

# --8<-- [start:example]
from litestar_mcp import MCP

mcp = MCP("my-mcp-server")


@mcp.resource(uri="app://system/status", name="system_status")
def get_status() -> dict[str, str]:
    """Get the current system status."""
    return {"status": "healthy", "uptime": "up"}


# --8<-- [end:example]
