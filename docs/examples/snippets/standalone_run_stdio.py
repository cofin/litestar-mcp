"""Snippet: running a standalone MCP server over stdio with MCPStdioContext."""

# --8<-- [start:example]
import os
from types import SimpleNamespace

from litestar import Request

from litestar_mcp import MCP, MCPStdioContext

mcp = MCP("stdio-server")


@mcp.tool()
def current_user(request: Request) -> dict[str, str]:
    """Return the current stdio identity."""
    return {"user_id": request.user.id}


app = mcp.app

if __name__ == "__main__":
    user_id = os.environ.get("MCP_USER_ID", "stdio")
    mcp.run(
        transport="stdio",
        stdio_context=MCPStdioContext(
            user=SimpleNamespace(id=user_id),
            auth={"sub": user_id},
        ),
    )
# --8<-- [end:example]
