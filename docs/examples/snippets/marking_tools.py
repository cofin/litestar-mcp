"""Snippet: marking a route as an MCP tool."""

# --8<-- [start:example]
from litestar import Litestar, get
from litestar.params import FromPath

from litestar_mcp import LitestarMCP


@get("/users/{user_id:int}", mcp_tool=True)
async def get_user(user_id: FromPath[int]) -> dict[str, int]:
    """Return a user by ID - exposed as the ``get_user`` MCP tool."""
    return {"id": user_id}


app = Litestar(route_handlers=[get_user], plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
