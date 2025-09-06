"""Litestar Model Context Protocol Integration Plugin.

A lightweight plugin that exposes Litestar routes as MCP tools and resources
using the opt attribute pattern. Routes marked with opt={"mcp_tool": "name"} or
opt={"mcp_resource": "name"} are automatically exposed through MCP endpoints.
"""

from litestar_mcp import schema
from litestar_mcp.__metadata__ import __version__
from litestar_mcp.config import MCPConfig
from litestar_mcp.plugin import LitestarMCP
from litestar_mcp.routes import MCPController

__all__ = ("LitestarMCP", "MCPConfig", "MCPController", "__version__", "schema")
