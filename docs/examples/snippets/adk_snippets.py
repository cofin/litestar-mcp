"""ADK client construction examples; ADK 2.8.0 cannot call the modern MCP endpoint."""

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams


def connect_simple() -> "McpToolset":
    """Construct a toolset without connecting; requires a compatible MCP server."""
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://api.example.com/mcp",
            headers={"Accept": "application/json, text/event-stream"},
        )
    )
    return toolset


def connect_with_auth() -> "McpToolset":
    """Construct a toolset with bearer headers; does not verify protocol compatibility."""
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://api.example.com/mcp",
            headers={
                "Authorization": "Bearer <your_valid_token>",
                "Accept": "application/json, text/event-stream",
            },
        )
    )
    return toolset


async def run_and_cleanup(toolset: "McpToolset") -> "None":
    """Clean up and close the toolset connection."""
    await toolset.close()
