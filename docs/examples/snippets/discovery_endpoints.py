"""Snippet: MCP capability discovery.

Referenced from docs/usage/discovery.rst.
MCP capability discovery uses ``server/discover`` on the POST transport.
"""

from litestar import Litestar

from litestar_mcp import LitestarMCP


def build() -> "Litestar":
    """Build a Litestar application with the LitestarMCP plugin.

    Discovery is served at:
      POST /mcp (server/discover)
    """
    return Litestar(route_handlers=[], plugins=[LitestarMCP()])
