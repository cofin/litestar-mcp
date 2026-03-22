"""Litestar Model Context Protocol Integration Plugin.

A lightweight plugin that exposes Litestar routes as MCP tools and resources
via JSON-RPC 2.0 over Streamable HTTP. Routes marked with opt={"mcp_tool": "name"}
or opt={"mcp_resource": "name"}, or decorated with @mcp_tool/@mcp_resource, are
automatically exposed through a single MCP endpoint.
"""

from litestar_mcp import schema
from litestar_mcp.__metadata__ import __version__
from litestar_mcp.config import MCPConfig
from litestar_mcp.decorators import mcp_resource, mcp_tool
from litestar_mcp.plugin import LitestarMCP
from litestar_mcp.routes import MCPController

__all__ = ("LitestarMCP", "MCPConfig", "MCPController", "__version__", "mcp_resource", "mcp_tool", "schema")
