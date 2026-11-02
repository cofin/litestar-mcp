"""Snippet: durable workflow execution on litestar-queues."""

from litestar import Litestar
from litestar.stores.memory import MemoryStore
from litestar_queues import QueueConfig, QueuePlugin, WorkerConfig

from litestar_mcp.agent.workflow import DynamicWorkflow, WorkflowContext
from litestar_mcp.queues import (
    DurableWorkflowEngine,
    WorkflowRegistry,
    WorkflowStore,
    chain_task_dependencies,
)


def step_extract(context: WorkflowContext) -> dict[str, int]:
    """Extract raw metrics."""
    return {"raw_count": 42}


def step_transform(context: WorkflowContext) -> dict[str, int]:
    """Transform extracted metrics."""
    raw = int(context.node_outputs["extract"]["raw_count"])
    return {"processed_count": raw * 2}


def create_engine() -> tuple[DurableWorkflowEngine, QueuePlugin]:
    """Create a configured DurableWorkflowEngine and QueuePlugin."""
    workflow = DynamicWorkflow(name="metrics_pipeline")
    workflow.add_node("extract", handler=step_extract)
    workflow.add_node("transform", handler=step_transform, depends_on=["extract"])

    registry = WorkflowRegistry([workflow])
    store = WorkflowStore(MemoryStore())
    engine = DurableWorkflowEngine(
        registry=registry,
        store=store,
        watchdog_interval=30.0,
        run_timeout_seconds=86_400.0,
    )

    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="local",
            worker=WorkerConfig(placement="external"),
            task_dependency_resolver=chain_task_dependencies(engine.dependencies()),
        )
    )
    return engine, queue_plugin


def build() -> Litestar:
    """Build a Litestar application configured for durable workflows."""
    _engine, queue_plugin = create_engine()
    return Litestar(plugins=[queue_plugin])
