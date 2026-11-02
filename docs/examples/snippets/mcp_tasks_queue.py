"""Snippet: distributed MCP Tasks using litestar-queues."""

from litestar import Litestar
from litestar_queues import QueuePlugin

from litestar_mcp import LitestarMCP, MCPConfig, MCPTaskConfig
from litestar_mcp.queues import QueueTaskExecutor


async def load_user(owner_id: str) -> dict[str, str]:
    """Rehydrate the request principal in the worker from the task's ``owner_id``."""
    return {"user_id": owner_id}


def build() -> Litestar:
    """Build a Litestar application whose MCP Tasks run on litestar-queues workers."""
    executor = QueueTaskExecutor(principal_loader=load_user)
    return Litestar(
        plugins=[
            QueuePlugin(),
            LitestarMCP(MCPConfig(tasks=MCPTaskConfig(executor=executor))),
        ],
    )
