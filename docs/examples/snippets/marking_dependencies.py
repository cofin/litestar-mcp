"""Snippet: request-scoped dependency injection. Referenced from docs/usage/dependencies.rst."""

from typing import Any

from litestar import Litestar, get
from litestar.di import Provide

from litestar_mcp import LitestarMCP


async def provide_current_user() -> dict[str, str]:
    return {"id": "u-1", "role": "admin"}


def build() -> Litestar:
    # start-example
    @get("/me", mcp_tool="whoami", dependencies={"current_user": Provide(provide_current_user)})
    async def whoami(current_user: "dict[str, Any]") -> "dict[str, Any]":
        """Return the resolved current user - exposed as the ``whoami`` MCP tool."""
        return current_user

    app = Litestar(route_handlers=[whoami], plugins=[LitestarMCP()])
    # end-example
    return app
