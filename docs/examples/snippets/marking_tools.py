"""Snippet: marking a route as an MCP tool. Referenced from docs/usage/marking.rst."""

from litestar import Litestar, get

from litestar_mcp import LitestarMCP


def build() -> Litestar:
    # start-example
    @get("/users/{user_id:int}", mcp_tool="get_user")
    async def get_user(user_id: int) -> dict[str, int]:
        """Return a user by ID - exposed as the ``get_user`` MCP tool."""
        return {"id": user_id}

    app = Litestar(route_handlers=[get_user], plugins=[LitestarMCP()])
    # end-example
    return app
