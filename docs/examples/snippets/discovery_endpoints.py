"""Snippet: well-known discovery endpoints.

Referenced from docs/usage/discovery.rst.
The plugin automatically serves the separate agent card and OAuth protected
resource documents. MCP capability discovery uses ``server/discover`` on the
POST transport.
"""

from litestar import Litestar

from litestar_mcp import LitestarMCP


def build() -> "Litestar":
    """Build a Litestar application with the LitestarMCP plugin.

    Discovery is served at:
      POST /mcp (server/discover)
      GET /.well-known/agent-card.json
      GET /.well-known/oauth-protected-resource
    """
    return Litestar(route_handlers=[], plugins=[LitestarMCP()])
