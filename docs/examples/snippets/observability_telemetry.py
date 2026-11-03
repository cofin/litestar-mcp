"""Snippet: OpenTelemetry TelemetryConfig, SpanManager, and MCP tool lifecycle hooks."""

# start-example
from typing import Any

from litestar import Litestar, Request, get

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.agent import Agent, AgentRuntime
from litestar_mcp.core.observability import SpanManager, TelemetryConfig

telemetry = TelemetryConfig(
    enable_spans=True,
    tracer_name="my_app.mcp",
    resource_attributes={
        "service.name": "notes-mcp",
        "deployment.environment": "production",
    },
)
span_manager = SpanManager(telemetry)


async def before_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    request: Request[Any, Any, Any],
    /,
) -> None:
    """Record tool invocation start on the request state."""
    request.state["active_mcp_tool"] = tool_name
    request.state["active_mcp_arg_keys"] = sorted(arguments.keys())


async def after_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    request: Request[Any, Any, Any],
    /,
    *,
    result: Any,
    exception: Exception | None,
    duration: float,
) -> None:
    """Emit structured audit log entries and span telemetry after each tool call."""
    _ = arguments, result
    with span_manager.span(
        f"mcp.tool.{tool_name}",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool_name,
            "mcp.tool.duration_s": round(duration, 6),
            "mcp.tool.failed": exception is not None,
        },
    ):
        request.app.logger.info(
            "mcp_tool=%s duration_s=%0.4f failed=%s",
            tool_name,
            duration,
            exception is not None,
        )


@get("/notes/summary", mcp_tool="summarize_notes")
async def summarize_notes() -> dict[str, int]:
    """Return summary counts for stored notes."""
    return {"total_notes": 12, "archived_notes": 3}


notes_agent = Agent(
    name="notes_assistant",
    model="gemini-3.8-flash",
    instructions="Summarize and audit notes for the current workspace.",
)
agent_runtime = AgentRuntime(notes_agent, span_manager=span_manager)

mcp_config = MCPConfig(
    before_tool_call=before_tool_call,
    after_tool_call=after_tool_call,
)

app = Litestar(
    route_handlers=[summarize_notes],
    plugins=[LitestarMCP(mcp_config), agent_runtime],
)
# end-example


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
