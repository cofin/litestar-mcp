from litestar_mcp import MCP

mcp = MCP("my-mcp-server", instructions="Exposes utility tools.")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Calculate the sum of two integers."""
    return a + b


# [start-run]
# Must expose the Litestar app instance globally so that the CLI can discover it
app = mcp.app

if __name__ == "__main__":
    # Boot the server using the default Server-Sent Events (SSE) transport
    mcp.run(transport="sse", port=8000)
# [end-run]
