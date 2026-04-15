"""Hello World Litestar MCP Plugin Example.

This example demonstrates the simplest possible integration of the Litestar MCP Plugin.
It shows how to add MCP capabilities to any Litestar application with just 3 lines of code.

The MCP plugin exposes your application's metadata through the MCP Streamable HTTP
transport surface so AI models can discover and interact with your API.
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


def build_app() -> Litestar:
    """Construct the Litestar app with the MCP plugin wired in.

    The body between the markers is what gets rendered via
    ``.. literalinclude:: ... :dedent: 4`` in the usage guide.
    """
    # start-example
    mcp_config = MCPConfig(name="Hello World API")
    app = Litestar(
        route_handlers=[hello, status],
        plugins=[LitestarMCP(mcp_config)],
    )
    # end-example
    return app


app = build_app()

# That's it! Your app now exposes MCP endpoints at /mcp and well-known metadata documents.

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
