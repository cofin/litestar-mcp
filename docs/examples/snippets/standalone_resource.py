from litestar_mcp import MCP

mcp = MCP("my-mcp-server")


@mcp.resource(uri="app://system/status", name="system_status")
def get_status() -> "dict[str, str]":
    """Get the current system status."""
    return {"status": "healthy", "uptime": "up"}
