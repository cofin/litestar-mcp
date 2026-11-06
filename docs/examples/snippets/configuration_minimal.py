"""Snippet: minimal LitestarMCP setup."""

# --8<-- [start:example]
from litestar import Litestar

from litestar_mcp import LitestarMCP

app = Litestar(plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
