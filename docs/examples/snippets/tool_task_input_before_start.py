"""Task-capable tool that completes an MRTR input round before task creation."""

# --8<-- [start:example]
from litestar import post

from litestar_mcp import MCPInputRequiredResult, get_mcp_request_context, mcp_tool


@mcp_tool(
    task_support="required",
    task_input_before_start=True,
)
@post("/reports")
async def publish_report() -> MCPInputRequiredResult | dict[str, str]:
    context = get_mcp_request_context()
    if not context.input_responses:
        return MCPInputRequiredResult(
            input_requests={
                "approval": {
                    "method": "elicitation/create",
                    "params": {"message": "Publish this report?"},
                }
            },
            request_state="integrity-protected-state",
        )
    return {"status": "published"}


# --8<-- [end:example]
