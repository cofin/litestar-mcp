"""Snippet: minimal LitestarMCP setup. Referenced from docs/usage/configuration.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP


def build() -> Litestar:
    # start-example
    app = Litestar(route_handlers=[], plugins=[LitestarMCP()])
    # end-example
    return app
