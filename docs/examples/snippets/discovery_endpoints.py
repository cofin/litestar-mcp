"""Snippet: well-known discovery endpoints. Referenced from docs/usage/discovery.rst.

The plugin automatically serves ``/.well-known/agent-card.json``,
``/.well-known/mcp-server.json``, and ``/.well-known/oauth-protected-resource``
once registered - no extra route handlers are required.
"""

from litestar import Litestar

from litestar_mcp import LitestarMCP


def build() -> Litestar:
    # start-example
    app = Litestar(route_handlers=[], plugins=[LitestarMCP()])
    # Discovery is now served at:
    #   GET /.well-known/agent-card.json
    #   GET /.well-known/mcp-server.json
    #   GET /.well-known/oauth-protected-resource
    # end-example
    return app
