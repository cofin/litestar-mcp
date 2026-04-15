"""Snippet: configuring an OIDC/JWKS provider. Referenced from docs/usage/auth.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthConfig, OIDCProviderConfig


def build() -> Litestar:
    # start-example
    provider = OIDCProviderConfig(
        issuer="https://auth.example.com",
        audience="https://api.example.com/mcp",
        algorithms=["RS256"],
    )
    config = MCPConfig(auth=MCPAuthConfig(providers=[provider]))
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
    # end-example
    return app
