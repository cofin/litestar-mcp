"""Snippet: distributed MCP Tasks using litestar-queues."""

from litestar import Litestar
from litestar.stores.memory import MemoryStore
from litestar_queues import QueueConfig, QueuePlugin, WorkerConfig

from litestar_mcp import LitestarMCP, MCPConfig, MCPTaskConfig
from litestar_mcp.queues import QueueTaskExecutor


async def load_user(owner_id: str) -> dict[str, str]:
    """Rehydrate the request principal in the worker from the task's ``owner_id``."""
    return {"user_id": owner_id, "scope": "task_runner"}


def build() -> Litestar:
    """Build and configure a Litestar application with distributed MCP Tasks."""
    executor = QueueTaskExecutor(principal_loader=load_user)
    task_config = MCPTaskConfig(
        store=MemoryStore(),
        executor=executor,
        default_ttl_ms=300_000,
        max_ttl_ms=3_600_000,
    )
    mcp_config = MCPConfig(tasks=task_config)
    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="local",
            worker=WorkerConfig(placement="external"),
            task_dependency_resolver=executor.dependencies(),
        )
    )
    return Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(mcp_config), queue_plugin],
    )
