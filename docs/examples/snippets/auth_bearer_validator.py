"""Snippet: MCPAuthBackend with a custom token validator. Referenced from docs/usage/auth.rst."""

from typing import Any

from litestar import Litestar
from litestar.middleware import DefineMiddleware

from litestar_mcp import LitestarMCP, MCPAuthBackend, MCPConfig
from litestar_mcp.auth import MCPAuthConfig


async def validate_token(token: str) -> "dict[str, Any] | None":
    """Return claims when ``token`` is valid, otherwise ``None``."""
    if token == "let-me-in":
        return {"sub": "demo-user", "scope": "mcp:read"}
    return None


def build() -> Litestar:
    # start-example
    app = Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(MCPConfig(auth=MCPAuthConfig(issuer="https://auth.example.com")))],
        middleware=[DefineMiddleware(MCPAuthBackend, token_validator=validate_token)],
    )
    # end-example
    return app
