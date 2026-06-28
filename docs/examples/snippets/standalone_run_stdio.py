from litestar_mcp import MCP

mcp = MCP("stdio-server")


@mcp.tool()
def echo(message: str) -> str:
    """Echo the message back."""
    return message


# Expose the app instance globally
app = mcp.app

# [start-run-stdio]
if __name__ == "__main__":
    # Boot the server over Stdio transport
    mcp.run(transport="stdio")
# [end-run-stdio]
