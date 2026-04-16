"""Snippet: MCPAuthBackend with OIDCProviderConfig. Referenced from docs/usage/auth.rst."""

from litestar import Litestar
from litestar.middleware import DefineMiddleware

from litestar_mcp import LitestarMCP, MCPAuthBackend, MCPConfig, OIDCProviderConfig
from litestar_mcp.auth import MCPAuthConfig


def build() -> Litestar:
    # start-example
    app = Litestar(
        route_handlers=[],
        plugins=[
            LitestarMCP(
                MCPConfig(
                    auth=MCPAuthConfig(
                        issuer="https://auth.example.com",
                        audience="https://api.example.com/mcp",
                    )
                )
            )
        ],
        middleware=[
            DefineMiddleware(
                MCPAuthBackend,
                providers=[
                    OIDCProviderConfig(
                        issuer="https://auth.example.com",
                        audience="https://api.example.com/mcp",
                        algorithms=["RS256"],
                    )
                ],
            )
        ],
    )
    # end-example
    return app
