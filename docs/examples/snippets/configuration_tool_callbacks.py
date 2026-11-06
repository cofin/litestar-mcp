"""Snippet: MCP tool-call callback configuration."""

# --8<-- [start:example]
from typing import Any

from litestar import Litestar, Request

from litestar_mcp import LitestarMCP, MCPConfig


async def after_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    request: Request[Any, Any, Any],
    *,
    result: Any,
    exception: Exception | None,
    duration: float,
) -> None:
    request.app.logger.info(
        "mcp tool=%s duration=%0.4f failed=%s",
        tool_name,
        duration,
        exception is not None,
    )


config = MCPConfig(after_tool_call=after_tool_call)
app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
