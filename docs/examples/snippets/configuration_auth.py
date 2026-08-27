"""Snippet: MCPConfig with an auth config. Referenced from docs/usage/auth.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPAuthConfig, MCPConfig


def build() -> "Litestar":
    # start-example
    auth = MCPAuthConfig(
        issuer="https://auth.example.com",
        audience="https://api.example.com/mcp",
        scopes={"mcp:read": "Read access to MCP resources"},
    )
    config = MCPConfig(auth=auth)
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
    # end-example
    return app
