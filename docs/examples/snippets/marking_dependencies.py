"""Snippet: request-scoped dependency injection."""

# --8<-- [start:example]
from typing import Any

from litestar import Litestar, get
from litestar.di import NamedDependency, Provide

from litestar_mcp import LitestarMCP


async def provide_current_user() -> dict[str, str]:
    return {"id": "u-1", "role": "admin"}


@get("/me", mcp_tool=True, dependencies={"current_user": Provide(provide_current_user)})
async def whoami(current_user: NamedDependency[dict[str, Any]]) -> dict[str, Any]:
    """Return the resolved current user - exposed as the ``whoami`` MCP tool."""
    return current_user


app = Litestar(route_handlers=[whoami], plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
