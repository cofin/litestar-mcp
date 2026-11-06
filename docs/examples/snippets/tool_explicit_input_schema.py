"""Tool with an explicit JSON Schema 2020-12 input contract."""

# --8<-- [start:example]
from litestar import get

from litestar_mcp import mcp_tool


@mcp_tool(
    input_schema={
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"region": {"type": "string"}},
        "required": ["region"],
        "additionalProperties": False,
    },
)
@get("/regions")
async def search_regions(region: str) -> list[str]:
    return [region]


# --8<-- [end:example]
