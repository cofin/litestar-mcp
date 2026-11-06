"""Snippet: accessing the underlying Litestar application from a standalone MCP instance."""

# --8<-- [start:example]
from litestar_mcp import MCP

mcp = MCP("my-mcp-server")
app = mcp.app
# --8<-- [end:example]
