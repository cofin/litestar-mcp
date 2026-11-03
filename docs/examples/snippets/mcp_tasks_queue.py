"""Snippet: distributed MCP Tasks using litestar-queues."""

from litestar import Litestar
from litestar_queues import QueuePlugin

from litestar_mcp import LitestarMCP


def build() -> Litestar:
    """Build a Litestar application whose MCP Tasks run on litestar-queues workers."""
    return Litestar(
        plugins=[
            QueuePlugin(),
            LitestarMCP(),
        ],
    )
