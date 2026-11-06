"""Snippet: minimal standalone MCP server."""

# --8<-- [start:example]
from litestar_mcp import MCP

mcp = MCP("my-mcp-server", instructions="Exposes utility tools.")


@mcp.tool(name="calculate_sum", description="Calculate the sum of two integers.")
def add(a: int, b: int) -> int:
    return a + b


# --8<-- [end:example]
