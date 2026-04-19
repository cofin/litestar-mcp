"""Snippet: using the mcp_tool/mcp_resource decorators. Referenced from docs/usage/marking_routes.rst."""

from litestar import Litestar, get

from litestar_mcp import LitestarMCP
from litestar_mcp.utils import mcp_tool


def build() -> Litestar:
    # start-example
    @mcp_tool(name="get_user")
    @get("/users/{user_id:int}")
    async def get_user(user_id: int) -> dict[str, int]:
        """Return a user by ID - exposed via the ``mcp_tool`` decorator."""
        return {"id": user_id}

    app = Litestar(route_handlers=[get_user], plugins=[LitestarMCP()])
    # end-example
    return app
