"""CLI subpackage for Litestar MCP and A2A integration."""

from litestar_mcp.cli.a2a import a2a_group
from litestar_mcp.cli.mcp import mcp_group

__all__ = ("a2a_group", "mcp_group")
