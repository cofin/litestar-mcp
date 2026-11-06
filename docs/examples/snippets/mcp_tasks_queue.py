"""Snippet: distributed MCP Tasks using litestar-queues."""

# --8<-- [start:example]
from litestar import Litestar
from litestar_queues import QueuePlugin

from litestar_mcp import LitestarMCP

app = Litestar(
    plugins=[
        QueuePlugin(),
        LitestarMCP(),
    ],
)
# --8<-- [end:example]


def build() -> Litestar:
    """Build a Litestar application whose MCP Tasks run on litestar-queues workers."""
    return Litestar(
        plugins=[
            QueuePlugin(),
            LitestarMCP(),
        ],
    )
