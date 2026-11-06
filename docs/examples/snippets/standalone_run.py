"""Snippet: running a standalone MCP server over HTTP."""

# --8<-- [start:example]
from litestar_mcp import MCP

mcp = MCP("my-mcp-server", instructions="Exposes utility tools.")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Calculate the sum of two integers."""
    return a + b


app = mcp.app

if __name__ == "__main__":
    mcp.run(port=8000)
# --8<-- [end:example]
