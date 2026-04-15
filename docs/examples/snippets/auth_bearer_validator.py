"""Snippet: custom bearer-token validator. Referenced from docs/usage/auth.rst."""

from typing import Any

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthConfig


async def validate_token(token: str) -> "dict[str, Any] | None":
    """Return claims when ``token`` is valid, otherwise ``None``."""
    if token == "let-me-in":
        return {"sub": "demo-user", "scope": "mcp:read"}
    return None


def build() -> Litestar:
    # start-example
    auth = MCPAuthConfig(token_validator=validate_token)
    config = MCPConfig(auth=auth)
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
    # end-example
    return app
