"""Basic Litestar MCP Plugin Example.

This example demonstrates the simplest possible integration of the Litestar MCP Plugin.
It shows how to add MCP capabilities to any Litestar application with just 3 lines of code.

The MCP plugin exposes your application's metadata through REST endpoints that AI models
can use to understand and interact with your API.
"""

from litestar import Litestar, get

from litestar_mcp import LitestarMCP, MCPConfig


@get("/")
async def hello() -> dict[str, str]:
    """A simple greeting endpoint."""
    return {"message": "Hello from Litestar!"}


@get("/status")
async def status() -> dict[str, str]:
    """API status endpoint."""
    return {"status": "healthy", "version": "1.0.0"}


# Step 1: Create MCP configuration (optional - uses defaults if not provided)
mcp_config = MCPConfig(
    server_name="Hello World API",  # Name shown to AI models
    debug_mode=True,  # Enables /mcp/debug endpoint for development
)

# Step 2: Add LitestarMCP to your Litestar app
app = Litestar(
    route_handlers=[hello, status],
    plugins=[LitestarMCP(mcp_config)],  # This enables MCP integration!
)

# That's it! Your app now exposes MCP endpoints at /mcp/*

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
