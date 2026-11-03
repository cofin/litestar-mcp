"""Snippet: MCP ToolAnnotations, HTTP verb inference, and route opt overrides."""

# start-example
from litestar import Litestar, delete, get, post, put

from litestar_mcp import LitestarMCP, MCPConfig, mcp_tool


@get("/items", mcp_tool="list_items")
async def list_items() -> list[str]:
    """List items with inferred readOnlyHint=True, destructiveHint=False, idempotentHint=True."""
    return ["espresso", "flat_white"]


@post("/items/search", mcp_tool="search_items", mcp_read_only=True)
async def search_items(query: str) -> list[str]:
    """Search items via POST while overriding readOnlyHint=True."""
    return [query]


@put("/items/{item_id:str}", mcp_tool="replace_item")
async def replace_item(item_id: str) -> dict[str, str]:
    """Replace an item with inferred readOnlyHint=False and idempotentHint=True."""
    return {"id": item_id, "status": "replaced"}


@mcp_tool(
    "inspect_item",
    read_only=True,
    annotations={
        "title": "Inspect Catalog Item",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@get("/items/{item_id:str}/inspect")
async def inspect_item(item_id: str) -> dict[str, str]:
    """Inspect a catalog item with explicit decorator annotations."""
    return {"id": item_id}


@delete(
    "/items/{item_id:str}",
    mcp_tool="delete_item",
    mcp_annotations={
        "title": "Delete Catalog Item",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    status_code=200,
)
async def delete_item(item_id: str) -> dict[str, str]:
    """Delete an item with inferred destructiveHint=True merged with mcp_annotations."""
    return {"deleted": item_id}


config = MCPConfig(infer_http_method_annotations=True)
app = Litestar(
    route_handlers=[list_items, search_items, replace_item, inspect_item, delete_item],
    plugins=[LitestarMCP(config)],
)
# end-example


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
