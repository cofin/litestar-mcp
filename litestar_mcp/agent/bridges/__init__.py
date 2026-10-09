"""Bidirectional protocol bridges for MCP and A2A."""

from __future__ import annotations

from litestar_mcp.agent.bridges.a2a import agent_to_a2a
from litestar_mcp.agent.bridges.mcp import (
    MCPHttpClient,
    agent_to_mcp,
    discover_mcp_tools,
    mcp_to_tools,
)

__all__ = (
    "MCPHttpClient",
    "agent_to_a2a",
    "agent_to_mcp",
    "discover_mcp_tools",
    "mcp_to_tools",
)
