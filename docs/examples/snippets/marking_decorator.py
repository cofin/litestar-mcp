"""Snippet: using the mcp_tool/mcp_resource decorators."""

# --8<-- [start:example]
from litestar import Litestar, get
from litestar.params import FromPath

from litestar_mcp import LitestarMCP
from litestar_mcp.utils import mcp_tool


@mcp_tool()
@get("/users/{user_id:int}")
async def get_user(user_id: FromPath[int]) -> dict[str, int]:
    """Return a user by ID - exposed via the ``mcp_tool`` decorator."""
    return {"id": user_id}


app = Litestar(route_handlers=[get_user], plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
