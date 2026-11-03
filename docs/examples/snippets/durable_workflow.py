"""Snippet: durable workflow execution on litestar-queues."""

from litestar import Litestar, post
from litestar_queues import QueuePlugin

from litestar_mcp.agent.workflow import DynamicWorkflow, WorkflowContext
from litestar_mcp.queues import DurableWorkflowEngine


def step_extract(context: WorkflowContext) -> dict[str, int]:
    """Extract raw metrics."""
    return {"raw_count": 42}


def step_transform(context: WorkflowContext) -> dict[str, int]:
    """Transform extracted metrics."""
    raw = int(context.node_outputs["extract"]["raw_count"])
    return {"processed_count": raw * 2}


metrics_pipeline = DynamicWorkflow(name="metrics_pipeline")
metrics_pipeline.add_node("extract", handler=step_extract)
metrics_pipeline.add_node("transform", handler=step_transform, depends_on=["extract"])


@post("/pipelines/metrics")
async def start_metrics_pipeline(workflow_engine: DurableWorkflowEngine) -> dict[str, str]:
    """Start a durable run; any worker sharing the queue backend executes it."""
    return {"run_id": await workflow_engine.start("metrics_pipeline")}


def build() -> Litestar:
    """Build a Litestar application configured for durable workflows."""
    return Litestar(
        route_handlers=[start_metrics_pipeline],
        plugins=[QueuePlugin(), DurableWorkflowEngine([metrics_pipeline])],
    )
