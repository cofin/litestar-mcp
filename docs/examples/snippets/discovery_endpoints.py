"""Snippet: well-known discovery endpoints.

Referenced from docs/usage/discovery.rst.
The plugin automatically serves ``/.well-known/agent-card.json``,
``/.well-known/mcp-server.json``, and ``/.well-known/oauth-protected-resource``
once registered - no extra route handlers are required.
"""

from litestar import Litestar

from litestar_mcp import LitestarMCP


def build() -> Litestar:
    """Build a Litestar application with the LitestarMCP plugin.

    Discovery is served at:
      GET /.well-known/agent-card.json
      GET /.well-known/mcp-server.json
      GET /.well-known/oauth-protected-resource
    """
    return Litestar(route_handlers=[], plugins=[LitestarMCP()])
