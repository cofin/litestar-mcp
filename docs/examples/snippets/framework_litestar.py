"""Snippet: framework-level Litestar registration. Referenced from docs/usage/framework.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP


def build() -> "Litestar":
    # start-example
    app = Litestar(plugins=[LitestarMCP()])
    # end-example
    return app
