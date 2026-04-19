"""Register a resource with an RFC 6570 Level 1 URI template."""

# start-example
from litestar import Litestar, get

from litestar_mcp import LitestarMCP


@get(
    "/workspaces/{workspace_id:str}/files/{file_id:str}",
    mcp_resource="workspace_file",
    mcp_resource_template="app://workspaces/{workspace_id}/files/{file_id}",
    sync_to_thread=False,
)
def read_workspace_file(workspace_id: str, file_id: str) -> dict[str, str]:
    """Return the concrete workspace/file payload."""
    return {"workspace": workspace_id, "file": file_id}


app = Litestar(route_handlers=[read_workspace_file], plugins=[LitestarMCP()])
# end-example
