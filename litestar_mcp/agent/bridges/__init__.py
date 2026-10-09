"""Bidirectional protocol bridges for MCP and A2A."""

from typing import TYPE_CHECKING, Any

from litestar_mcp.agent.bridges.mcp import (
    agent_to_mcp,
    discover_mcp_tools,
    mcp_to_tools,
)

if TYPE_CHECKING:
    from litestar_mcp.agent.bridges.a2a import agent_to_a2a

__all__ = (
    "agent_to_a2a",
    "agent_to_mcp",
    "discover_mcp_tools",
    "mcp_to_tools",
)


def __getattr__(name: str) -> Any:
    if name == "agent_to_a2a":
        from litestar_mcp.agent.bridges.a2a import agent_to_a2a

        return agent_to_a2a
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
