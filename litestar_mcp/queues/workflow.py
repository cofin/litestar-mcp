"""Durable workflow execution engine and tasks for litestar-queues."""

import time
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
import msgspec
from litestar.di import Provide
from litestar.exceptions import ImproperlyConfiguredException, SerializationException
from litestar.plugins import InitPluginProtocol
from litestar_queues import QueuePlugin, task

from litestar_mcp.agent.spec import Agent
from litestar_mcp.agent.workflow import (
    DynamicWorkflow,
    WorkflowContext,
    WorkflowEngine,
    run_workflow_node,
    should_skip,
)
from litestar_mcp.queues.dependencies import TaskDependencyChain
from litestar_mcp.queues.workflow_store import (
    NodeRecord,
    RunStatus,
    WorkflowRun,
    WorkflowStore,
)
from litestar_mcp.utils import resolve_app_store

if TYPE_CHECKING:
    from litestar.config.app import AppConfig
    from litestar.stores.base import Store
    from litestar_queues import QueueService

    from litestar_mcp.agent.runtime import AgentRuntime

__all__ = (
    "ADVANCE_TASK",
    "EXECUTE_NODE_TASK",
    "WATCHDOG_TASK",
    "DurableWorkflowEngine",
    "WorkflowRegistry",
    "advance_workflow",
    "execute_workflow_node",
    "watch_workflow",
)

ADVANCE_TASK = "litestar_mcp.workflow.advance"
EXECUTE_NODE_TASK = "litestar_mcp.workflow.execute_node"
WATCHDOG_TASK = "litestar_mcp.workflow.watchdog"
WORKFLOW_TASKS = frozenset({ADVANCE_TASK, EXECUTE_NODE_TASK, WATCHDOG_TASK})
WORKFLOW_STORE_NAME = "litestar_mcp_workflows"


class WorkflowRegistry:
    """Maps workflow names to DynamicWorkflow definitions available in every worker."""

    def __init__(self, workflows: Iterable[DynamicWorkflow]) -> None:
        """Initialize registry and validate that all workflows are valid DAGs.

        Args:
            workflows: Iterable of DynamicWorkflow instances.

        Raises:
            ImproperlyConfiguredException: If a duplicate workflow name is encountered.
        """
        self._workflows: dict[str, DynamicWorkflow] = {}
        for wf in workflows:
            WorkflowEngine.validate_dependencies(wf)
            if wf.name in self._workflows:
                msg = f"Duplicate workflow name registered: {wf.name!r}"
                raise ImproperlyConfiguredException(msg)
            self._workflows[wf.name] = wf

    def get(self, name: str) -> DynamicWorkflow:
        """Retrieve a registered DynamicWorkflow by name.

        Args:
            name: Workflow identifier.

        Returns:
            The registered DynamicWorkflow.

        Raises:
            KeyError: If the workflow is not registered.
        """
        if name not in self._workflows:
            msg = f"Unknown workflow: {name!r}"
            raise KeyError(msg)
        return self._workflows[name]


class DurableWorkflowEngine(InitPluginProtocol):
    """Runs DynamicWorkflow definitions on litestar-queues with Store-backed state.

    Register the engine as a Litestar plugin next to ``QueuePlugin``. On app
    init it attaches its task dependencies to the queue configuration, resolves
    its store from the application's store registry, and exposes itself to
    route handlers as the ``workflow_engine`` dependency.
    """

    def __init__(
        self,
        workflows: Iterable[DynamicWorkflow],
        *,
        store: "Store | None" = None,
        runtime_factory: Callable[[Agent], "AgentRuntime"] | None = None,
        queue: str = "workflow",
        watchdog_interval: float | None = 30.0,
        run_timeout_seconds: float = 86_400.0,
    ) -> None:
        """Initialize DurableWorkflowEngine.

        Args:
            workflows: DynamicWorkflow definitions available to every worker.
            store: Optional Litestar ``Store`` for run and node records. Defaults to
                the application's ``stores`` registry entry named
                ``litestar_mcp_workflows``, so a shared registry (for example Redis)
                configured on ``Litestar(stores=...)`` applies automatically.
            runtime_factory: Optional callable mapping Agent to AgentRuntime.
            queue: Target queue name for workflow tasks.
            watchdog_interval: Optional delay in seconds between watchdog checks.
            run_timeout_seconds: Maximum total duration before timing out a run.
        """
        self.registry = WorkflowRegistry(workflows)
        self._store: WorkflowStore | None = WorkflowStore(store) if store is not None else None
        self.runtime_factory = runtime_factory
        self._queue = queue
        self.watchdog_interval = watchdog_interval
        self.run_timeout_seconds = run_timeout_seconds
        self._queue_plugin: QueuePlugin | None = None

    @property
    def store(self) -> WorkflowStore:
        """Durable run and node records."""
        if self._store is None:
            msg = "DurableWorkflowEngine has no store; register it in Litestar(plugins=[...]) or pass store="
            raise ImproperlyConfiguredException(msg)
        return self._store

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Wire task dependencies, the default store, and the ``workflow_engine`` dependency.

        Returns:
            The updated application configuration.
        """
        self._queue_plugin = TaskDependencyChain.attach(
            app_config,
            self.resolve_task_dependencies,
            owner="DurableWorkflowEngine",
        )
        if self._store is None:
            self._store = WorkflowStore(resolve_app_store(app_config, WORKFLOW_STORE_NAME))
        app_config.dependencies.setdefault("workflow_engine", Provide(self._provide_engine, sync_to_thread=False))
        app_config.signature_namespace.setdefault("DurableWorkflowEngine", DurableWorkflowEngine)
        return app_config

    async def resolve_task_dependencies(
        self,
        task: Any,
        record: Any = None,
        task_context: Any = None,
    ) -> Mapping[str, object]:
        """Inject this engine into the workflow tasks.

        Returns:
            ``{"workflow_engine": self}`` for workflow tasks, otherwise an empty mapping.
        """
        if getattr(task, "name", None) in WORKFLOW_TASKS:
            return {"workflow_engine": self}
        return {}

    async def start(self, workflow: str, initial_state: dict[str, Any] | None = None) -> str:
        """Start a new durable workflow run and enqueue initial advance task.

        Args:
            workflow: Registered workflow name.
            initial_state: Optional initial state dictionary.

        Returns:
            The generated run_id string.

        Raises:
            ImproperlyConfiguredException: If immediate execution backend is used with watchdog.
        """
        self.registry.get(workflow)
        service = self._queue_service()
        execution_backend = getattr(service.config, "execution_backend", None)
        if execution_backend == "immediate" and self.watchdog_interval is not None:
            msg = (
                "DurableWorkflowEngine watchdog requires a non-immediate execution backend (e.g. local, redis, sqlspec)"
            )
            raise ImproperlyConfiguredException(msg)

        run_id = uuid4().hex
        run = WorkflowRun(
            run_id=run_id,
            workflow=workflow,
            state=dict(initial_state or {}),
        )
        await self.store.create_run(run)
        await service.enqueue(advance_workflow, run_id=run_id, queue=self._queue)
        if self.watchdog_interval is not None:
            await service.enqueue(
                watch_workflow,
                run_id=run_id,
                tick=1,
                key=f"wf-watchdog:{run_id}:1",
                run_after=self.watchdog_interval,
                queue=self._queue,
            )
        return run_id

    async def snapshot(self, run_id: str) -> tuple[WorkflowRun, WorkflowContext]:
        """Load the durable run record and reconstructed WorkflowContext.

        Args:
            run_id: Unique workflow run identifier.

        Returns:
            Tuple of (WorkflowRun, WorkflowContext).

        Raises:
            KeyError: If the workflow run does not exist.
        """
        run = await self.store.get_run(run_id)
        if run is None:
            msg = f"Unknown workflow run: {run_id!r}"
            raise KeyError(msg)
        wf = self.registry.get(run.workflow)
        nodes = await self.store.get_nodes(run_id, wf.nodes.keys())
        ctx = self.store.to_context(run, nodes)
        return run, ctx

    async def advance(self, run_id: str) -> RunStatus | None:
        """Advance a durable workflow run by scheduling ready nodes or finalizing terminal state.

        Args:
            run_id: Unique workflow run identifier.

        Returns:
            The updated RunStatus, or None if run does not exist.
        """
        run = await self.store.get_run(run_id)
        if run is None:
            return None
        if run.status in ("completed", "failed"):
            return run.status

        wf = self.registry.get(run.workflow)
        nodes = await self.store.get_nodes(run_id, wf.nodes.keys())
        service = self._queue_service()

        for name, node in wf.nodes.items():
            if name in nodes:
                continue
            if all(dep in nodes for dep in node.depends_on):
                await service.enqueue(
                    execute_workflow_node,
                    run_id=run_id,
                    node=name,
                    key=f"wf-node:{run_id}:{name}",
                    queue=self._queue,
                )

        if len(nodes) == len(wf.nodes):
            any_failed = any(r.status == "failed" for r in nodes.values())
            status: RunStatus = "failed" if any_failed else "completed"
            error: str | None = None
            if any_failed:
                for r in nodes.values():
                    if r.status == "failed" and r.error:
                        error = r.error
                        break
            updated_run = await self.store.set_run_status(run_id, status, error=error)
            for name in wf.nodes:
                await service.reset_task_identity(f"wf-node:{run_id}:{name}")
            return updated_run.status

        return run.status

    async def execute_node(self, run_id: str, node: str) -> None:
        """Execute a single workflow node, record its outcome, and re-enqueue advance.

        Args:
            run_id: Unique workflow run identifier.
            node: Target node name to execute.
        """
        existing = await self.store.get_node(run_id, node)
        if existing is not None:
            return

        run = await self.store.get_run(run_id)
        if run is None:
            return

        wf = self.registry.get(run.workflow)
        node_def = wf.nodes[node]
        nodes = await self.store.get_nodes(run_id, wf.nodes.keys())
        ctx = self.store.to_context(run, nodes)

        if should_skip(node_def, ctx):
            await self.store.put_node(run_id, NodeRecord(node=node, status="skipped"))
        else:
            try:
                with anyio.fail_after(node_def.timeout_seconds):
                    output = await run_workflow_node(
                        node_def,
                        ctx,
                        runtime_factory=self.runtime_factory,
                    )
                try:
                    await self.store.put_node(
                        run_id,
                        NodeRecord(node=node, status="completed", output=output),
                    )
                except (TypeError, msgspec.EncodeError, SerializationException):
                    await self.store.put_node(
                        run_id,
                        NodeRecord(
                            node=node,
                            status="failed",
                            output=None,
                            error="TypeError: node output is not JSON serializable",
                        ),
                    )
            except Exception as exc:
                await self.store.put_node(
                    run_id,
                    NodeRecord(
                        node=node,
                        status="failed",
                        output=None,
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                )

        service = self._queue_service()
        await service.enqueue(advance_workflow, run_id=run_id, queue=self._queue)

    async def watch(self, run_id: str, tick: int = 1) -> RunStatus | None:
        """Execute one watchdog tick for a durable run.

        Args:
            run_id: Unique workflow run identifier.
            tick: Current tick iteration number.

        Returns:
            The resulting RunStatus, or None if run does not exist.
        """
        run = await self.store.get_run(run_id)
        if run is None:
            return None
        if run.status in ("completed", "failed"):
            return run.status

        service = self._queue_service()
        if time.time() - run.created_at >= self.run_timeout_seconds:
            updated_run = await self.store.set_run_status(run_id, "failed", error="workflow timed out")
            wf = self.registry.get(run.workflow)
            for name in wf.nodes:
                await service.reset_task_identity(f"wf-node:{run_id}:{name}")
            return updated_run.status

        current_status = await self.advance(run_id)
        if current_status == "running" and self.watchdog_interval is not None:
            await service.enqueue(
                watch_workflow,
                run_id=run_id,
                tick=tick + 1,
                key=f"wf-watchdog:{run_id}:{tick + 1}",
                run_after=self.watchdog_interval,
                queue=self._queue,
            )
        return current_status

    def _provide_engine(self) -> "DurableWorkflowEngine":
        """Return this engine for the ``workflow_engine`` dependency."""
        return self

    def _queue_service(self) -> "QueueService":
        """Return the queue service of the application's ``QueuePlugin``.

        Inside the application lifespan this is the plugin's opened service. In
        standalone worker processes, which never run the application lifespan,
        the plugin builds a service bound to its configured backend.

        Raises:
            ImproperlyConfiguredException: If the engine was never registered as a plugin.
        """
        if self._queue_plugin is None:
            msg = "DurableWorkflowEngine is not registered; add it to Litestar(plugins=[...]) next to QueuePlugin"
            raise ImproperlyConfiguredException(msg)
        return self._queue_plugin.get_service()


@task(ADVANCE_TASK, queue="workflow", retries=3)
async def advance_workflow(run_id: str, workflow_engine: DurableWorkflowEngine) -> str | None:
    """Advance one durable run.

    Args:
        run_id: Unique workflow run identifier.
        workflow_engine: Injected DurableWorkflowEngine instance.

    Returns:
        The updated run status string, or None.
    """
    return await workflow_engine.advance(run_id)


@task(EXECUTE_NODE_TASK, queue="workflow", retries=0, unique_until="forever", unique_by="arguments")
async def execute_workflow_node(run_id: str, node: str, workflow_engine: DurableWorkflowEngine) -> None:
    """Execute one node of one durable run exactly once per reservation.

    Args:
        run_id: Unique workflow run identifier.
        node: Node name to execute.
        workflow_engine: Injected DurableWorkflowEngine instance.
    """
    await workflow_engine.execute_node(run_id, node)


@task(WATCHDOG_TASK, queue="workflow", retries=3)
async def watch_workflow(run_id: str, tick: int, workflow_engine: DurableWorkflowEngine) -> str | None:
    """Re-advance a non-terminal run and schedule the next tick.

    Args:
        run_id: Unique workflow run identifier.
        tick: Current tick iteration number.
        workflow_engine: Injected DurableWorkflowEngine instance.

    Returns:
        The updated run status string, or None.
    """
    return await workflow_engine.watch(run_id, tick)
