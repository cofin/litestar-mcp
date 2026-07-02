"""Snippet: validate domain object access for MCP tools."""

# start-example
from typing import Any

from litestar import Litestar, Request, get
from litestar.di import Provide
from litestar.exceptions import PermissionDeniedException

from litestar_mcp import LitestarMCP


async def require_workspace_owner(
    workspace_owner_id: "str",
    request: "Request[Any, Any, Any]",
) -> "None":
    """Reject a request for a workspace not owned by the authenticated user."""
    user_id = getattr(request.user, "id", None) or getattr(request.user, "sub", None)
    if user_id != workspace_owner_id:
        msg = "workspace owner mismatch"
        raise PermissionDeniedException(msg)


@get(
    "/workspaces/{workspace_owner_id:str}/export",
    mcp_tool="export_workspace",
    dependencies={"_owner_check": Provide(require_workspace_owner)},
    sync_to_thread=False,
)
async def export_workspace(workspace_owner_id: "str", _owner_check: "None") -> "dict[str, str]":
    """Export a workspace after the dependency authorizes the object ID."""
    return {"workspace_id": workspace_owner_id, "status": "queued"}


app = Litestar(route_handlers=[export_workspace], plugins=[LitestarMCP()])
# end-example
