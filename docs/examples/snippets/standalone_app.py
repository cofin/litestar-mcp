from litestar_mcp import MCP

mcp = MCP("my-mcp-server")

# [start-app]
# Access the underlying Litestar application
app = mcp.app
# [end-app]
