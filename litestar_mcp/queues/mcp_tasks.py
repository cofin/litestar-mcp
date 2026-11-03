"""Distributed MCP task execution on litestar-queues."""

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any
from uuid import UUID

import anyio
import msgspec
from litestar.exceptions import ImproperlyConfiguredException
from litestar_queues.events.context import get_current_task_context
from litestar_queues.task import task

from litestar_mcp.mcp.tasks import ToolTaskCall
from litestar_mcp.queues.dependencies import TaskDependencyChain

if TYPE_CHECKING:
    from litestar.config.app import AppConfig
    from litestar_queues import QueuePlugin
    from litestar_queues.service import QueueService

    from litestar_mcp.mcp.tasks import MCPTaskRuntime, TaskRecord

__all__ = ("RUN_TOOL_TASK", "QueueTaskExecutor", "run_mcp_tool_task")

RUN_TOOL_TASK = "litestar_mcp.mcp.run_tool_task"


class QueueTaskExecutor:
    """Run MCP Tasks attempts on litestar-queues.

    ``LitestarMCP`` calls :meth:`configure` during app init, which attaches the
    executor's task dependencies to the application's ``QueuePlugin``; no queue
    wiring is needed beyond registering ``QueuePlugin`` on the same app.

    Args:
        principal_loader: Optional callback rehydrating ``scope["user"]`` from a
            task ``owner_id`` in the worker. When omitted, ``LitestarMCP``
            automatically rehydrates ``scope["user"]``, ``scope["auth"]``, and
            ``scope["litestar_security"]`` from the authenticated principal
            snapshot and the application's existing Litestar dependencies.
        queue: Default queue name for tool attempts (overridable per controller
            or route via ``mcp_queue`` / ``@mcp_tool(queue=...)``).
        timeout: Per-attempt timeout in seconds.
    """

    def __init__(
        self,
        *,
        principal_loader: Callable[[str], Awaitable[Any]] | None = None,
        queue: str = "mcp",
        timeout: int = 3600,
    ) -> None:
        self._principal_loader = principal_loader
        self._queue = queue
        self._timeout = timeout
        self._runtime: MCPTaskRuntime | None = None
        self._queue_plugin: QueuePlugin | None = None

    @property
    def runtime(self) -> "MCPTaskRuntime":
        """Collaborators bound at application startup."""
        if self._runtime is None:
            msg = (
                "QueueTaskExecutor is not bound; register LitestarMCP on the application and keep "
                "WorkerConfig.run_app_lifespan enabled so standalone queue workers run application startup"
            )
            raise RuntimeError(msg)
        return self._runtime

    def configure(self, app_config: "AppConfig") -> None:
        """Attach the executor's task dependencies to the application's ``QueuePlugin``."""
        self._queue_plugin = TaskDependencyChain.attach(
            app_config,
            self.resolve_task_dependencies,
            owner="QueueTaskExecutor",
        )

    def bind(self, runtime: "MCPTaskRuntime") -> None:
        """Bind runtime collaborators to the executor."""
        self._runtime = runtime

    async def resolve_task_dependencies(
        self,
        task: Any,
        record: Any = None,
        task_context: Any = None,
    ) -> Mapping[str, object]:
        """Inject this executor into tool task attempts.

        Returns:
            ``{"mcp_task_executor": self}`` for tool attempts, otherwise an empty mapping.
        """
        if getattr(task, "name", None) == RUN_TOOL_TASK:
            return {"mcp_task_executor": self}
        return {}

    async def submit(self, record: "TaskRecord", call: ToolTaskCall) -> None:
        """Enqueue the first tool call attempt on litestar-queues."""
        await self._enqueue_attempt(call)

    async def resume(self, call: ToolTaskCall) -> None:
        """Enqueue the next attempt after client input responses arrive."""
        await self._enqueue_attempt(call)

    async def cancel(self, record: "TaskRecord") -> None:
        """Cancel the task's current attempt and persist ``cancelled`` when this replica wins."""
        if record.is_terminal():
            return
        store = self.runtime.task_store
        job_id = await store.get_job_id(record.task_id)
        won = False
        if job_id is not None:
            service = self._queue_service()
            won = await service.cancel_task(UUID(job_id), include_running=True)
        if won or job_id is None or record.status == "input_required":
            await store.mark_cancelled(record.task_id)

    async def load_principal(self, owner_id: str | None) -> Any:
        """Rehydrate user principal from owner_id."""
        if owner_id is None or self._principal_loader is None:
            return None
        return await self._principal_loader(owner_id)

    async def _enqueue_attempt(self, call: ToolTaskCall) -> None:
        """Enqueue one attempt keyed by task id and attempt number, then record its job id."""
        result = await self._queue_service().enqueue(
            run_mcp_tool_task,
            call=msgspec.to_builtins(call),
            key=f"mcp-task:{call.task_id}:{call.attempt}",
            queue=call.queue or self._queue,
            timeout=self._timeout,
        )
        await self.runtime.task_store.set_job_id(call.task_id, str(result.id))

    def _queue_service(self) -> "QueueService":
        """Return the queue service bound to the started application's state.

        That is the web process's opened service, or the worker's own service in
        standalone worker processes, where litestar-queues enters the application
        lifespan before claiming tasks.
        """
        if self._queue_plugin is None:
            msg = (
                "QueueTaskExecutor is not configured; set it on MCPTaskConfig(executor=...) of a registered LitestarMCP"
            )
            raise ImproperlyConfiguredException(msg)
        return self._queue_plugin.config.get_service(self.runtime.app.state)


@task(RUN_TOOL_TASK, queue="mcp", retries=0)
async def run_mcp_tool_task(call: dict[str, Any], mcp_task_executor: QueueTaskExecutor) -> dict[str, Any]:
    """Run one MCP Tasks attempt in the worker and return the record status."""
    tool_call = msgspec.convert(call, ToolTaskCall)
    user = await mcp_task_executor.load_principal(tool_call.owner_id)
    context = get_current_task_context()
    async with anyio.create_task_group() as group:
        if context is not None:
            group.start_soon(_cancel_on_signal, context, group.cancel_scope)
        await mcp_task_executor.runtime.run_attempt(tool_call, user)
        group.cancel_scope.cancel()
    task_id = tool_call.task_id
    record = await mcp_task_executor.runtime.task_store.get(task_id, None)
    return {"task_id": task_id, "status": record.status}


async def _cancel_on_signal(context: Any, scope: anyio.CancelScope) -> None:
    """Wait for durable cancellation signal on the task context and cancel the attempt scope."""
    await context.wait_cancelled()
    scope.cancel()
