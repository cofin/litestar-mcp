"""Unit tests for WorkflowStore, durable run models, and DurableWorkflowEngine."""

from datetime import datetime, timedelta, timezone
from typing import Any

import anyio
import pytest

pytest.importorskip("litestar_queues")

from litestar import Litestar
from litestar.exceptions import ImproperlyConfiguredException
from litestar.stores.memory import MemoryStore
from litestar.testing import AsyncTestClient
from litestar_queues import QueueConfig, QueuePlugin, QueueService, WorkerConfig
from litestar_queues.worker import Worker

from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.spec import Agent
from litestar_mcp.agent.workflow import (
    DynamicWorkflow,
    StepResult,
    WorkflowContext,
    WorkflowNode,
)
from litestar_mcp.queues.workflow import (
    DurableWorkflowEngine,
    WorkflowRegistry,
    execute_workflow_node,
    watch_workflow,
)
from litestar_mcp.queues.workflow_store import (
    NodeRecord,
    WorkflowRun,
    WorkflowStore,
)


class RecordingStore(MemoryStore):
    """MemoryStore subclass that records every set call with its expires_in argument."""

    def __init__(self) -> None:
        """Initialize RecordingStore."""
        super().__init__()
        self.set_calls: list[tuple[str, int | timedelta | None]] = []

    async def set(
        self,
        key: str,
        value: str | bytes,
        expires_in: int | timedelta | None = None,
    ) -> None:
        """Record key and expires_in before delegating to superclass."""
        self.set_calls.append((key, expires_in))
        await super().set(key, value, expires_in=expires_in)


def _make_engine_and_app(
    workflows: list[DynamicWorkflow],
    store: WorkflowStore | None = None,
    runtime_factory: Any = None,
    watchdog_interval: float | None = 30.0,
    run_timeout_seconds: float = 86_400.0,
    execution_backend: Any = "local",
) -> tuple[Litestar, DurableWorkflowEngine, QueuePlugin]:
    """Create a Litestar test application with configured DurableWorkflowEngine and QueuePlugin."""
    registry = WorkflowRegistry(workflows)
    wf_store = store or WorkflowStore(MemoryStore())
    engine = DurableWorkflowEngine(
        registry=registry,
        store=wf_store,
        runtime_factory=runtime_factory,
        watchdog_interval=watchdog_interval,
        run_timeout_seconds=run_timeout_seconds,
    )
    plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend=execution_backend,
            worker=WorkerConfig(placement="external"),
            task_dependency_resolver=engine.dependencies(),
        )
    )
    app = Litestar(plugins=[plugin])
    return app, engine, plugin


async def _drain(service: QueueService, config: WorkerConfig, engine: DurableWorkflowEngine, run_id: str) -> None:
    """Drain queue tasks until workflow run reaches a terminal state or timeout."""
    worker = Worker(service, config)
    for _ in range(50):
        run, _ = await engine.snapshot(run_id)
        if run.status != "running":
            break
        await worker.run_once()
        await anyio.sleep(0.01)
    await worker.stop()


@pytest.mark.anyio
async def test_workflow_store_round_trip() -> None:
    """WorkflowRun and NodeRecord instances round trip cleanly through WorkflowStore."""
    store = MemoryStore()
    wf_store = WorkflowStore(store=store, ttl_seconds=3600)

    run = WorkflowRun(
        run_id="run-1",
        workflow="etl",
        state={"raw_input": 123},
    )
    await wf_store.create_run(run)

    loaded_run = await wf_store.get_run("run-1")
    assert loaded_run is not None
    assert loaded_run.run_id == "run-1"
    assert loaded_run.workflow == "etl"
    assert loaded_run.state == {"raw_input": 123}
    assert loaded_run.status == "running"
    assert loaded_run.error is None

    node_extract = NodeRecord(node="extract", status="completed", output={"rows": 10})
    node_transform = NodeRecord(node="transform", status="completed", output={"cleaned": 10})
    await wf_store.put_node("run-1", node_extract)
    await wf_store.put_node("run-1", node_transform)

    single_node = await wf_store.get_node("run-1", "extract")
    assert single_node is not None
    assert single_node.node == "extract"
    assert single_node.status == "completed"
    assert single_node.output == {"rows": 10}

    nodes = await wf_store.get_nodes("run-1", ["extract", "transform", "load"])
    assert set(nodes.keys()) == {"extract", "transform"}
    assert nodes["extract"].output == {"rows": 10}
    assert nodes["transform"].output == {"cleaned": 10}


@pytest.mark.anyio
async def test_set_run_status_is_monotonic() -> None:
    """Terminal run status never moves to another status."""
    store = MemoryStore()
    wf_store = WorkflowStore(store=store)

    run = WorkflowRun(run_id="run-mono", workflow="pipeline")
    await wf_store.create_run(run)

    completed_run = await wf_store.set_run_status("run-mono", "completed")
    assert completed_run.status == "completed"

    second_run = await wf_store.set_run_status("run-mono", "failed", error="late failure")
    assert second_run.status == "completed"
    assert second_run.error is None

    persisted = await wf_store.get_run("run-mono")
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.error is None


@pytest.mark.anyio
async def test_put_node_is_write_once() -> None:
    """Subsequent put_node calls for the same node do not overwrite existing records."""
    store = MemoryStore()
    wf_store = WorkflowStore(store=store)

    run = WorkflowRun(run_id="run-once", workflow="pipeline")
    await wf_store.create_run(run)

    first_record = NodeRecord(node="n1", status="completed", output="initial")
    await wf_store.put_node("run-once", first_record)

    duplicate_record = NodeRecord(node="n1", status="failed", error="retry failed")
    await wf_store.put_node("run-once", duplicate_record)

    loaded = await wf_store.get_node("run-once", "n1")
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.output == "initial"
    assert loaded.error is None


@pytest.mark.anyio
async def test_put_node_rejects_unserializable_output() -> None:
    """put_node propagates encoding errors for non-JSON-serializable outputs."""
    store = MemoryStore()
    wf_store = WorkflowStore(store=store)

    run = WorkflowRun(run_id="run-err", workflow="pipeline")
    await wf_store.create_run(run)

    bad_record = NodeRecord(node="bad", status="completed", output=object())
    with pytest.raises((TypeError, Exception)):
        await wf_store.put_node("run-err", bad_record)


@pytest.mark.anyio
async def test_every_set_has_ttl() -> None:
    """Every write to the underlying Store carries expires_in equal to ttl_seconds."""
    recording_store = RecordingStore()
    wf_store = WorkflowStore(store=recording_store, ttl_seconds=1200)

    run = WorkflowRun(run_id="run-ttl", workflow="pipeline")
    await wf_store.create_run(run)
    await wf_store.set_run_status("run-ttl", "running")
    await wf_store.put_node("run-ttl", NodeRecord(node="n1", status="completed"))

    assert len(recording_store.set_calls) == 3
    for _key, expires_in in recording_store.set_calls:
        assert expires_in == 1200


@pytest.mark.anyio
def test_to_context_matches_in_process_contract() -> None:
    """to_context constructs WorkflowContext matching WorkflowEngine expectations."""
    wf_store = WorkflowStore(store=MemoryStore())

    run = WorkflowRun(
        run_id="run-ctx",
        workflow="pipeline",
        state={"initial": 42},
    )
    nodes = {
        "step_ok": NodeRecord(node="step_ok", status="completed", output="val1"),
        "step_skip": NodeRecord(node="step_skip", status="skipped", output=None),
        "step_fail": NodeRecord(node="step_fail", status="failed", output=None, error="boom"),
    }

    ctx = wf_store.to_context(run, nodes)
    assert isinstance(ctx, WorkflowContext)
    assert ctx.run_id == "run-ctx"
    assert ctx.state == {"initial": 42}
    assert ctx.node_outputs == {"step_ok": "val1"}

    assert len(ctx.step_results) == 3
    ok_res = ctx.step_results["step_ok"]
    assert isinstance(ok_res, StepResult)
    assert ok_res.node_name == "step_ok"
    assert ok_res.status == "completed"
    assert ok_res.output == "val1"
    assert ok_res.error is None

    skip_res = ctx.step_results["step_skip"]
    assert skip_res.status == "skipped"
    assert skip_res.output is None

    fail_res = ctx.step_results["step_fail"]
    assert fail_res.status == "failed"
    assert fail_res.output is None
    assert fail_res.error == "boom"


@pytest.mark.anyio
async def test_duplicate_run_rejected() -> None:
    """create_run raises ValueError if a run with the given run_id already exists."""
    store = MemoryStore()
    wf_store = WorkflowStore(store=store)

    run = WorkflowRun(run_id="dup-run", workflow="pipeline")
    await wf_store.create_run(run)

    with pytest.raises(ValueError, match="already exists"):
        await wf_store.create_run(run)


@pytest.mark.anyio
async def test_durable_run_completes_dag() -> None:
    """DAG runs to completion across worker steps and aggregates state."""
    counters = {"a": 0, "b": 0, "c": 0, "d": 0}

    def _step_a(c: WorkflowContext) -> int:
        counters["a"] += 1
        return 10

    def _step_b(c: WorkflowContext) -> int:
        counters["b"] += 1
        return int(c.node_outputs["a"]) * 2

    def _step_c(c: WorkflowContext) -> int:
        counters["c"] += 1
        return int(c.node_outputs["a"]) + 5

    def _step_d(c: WorkflowContext) -> int:
        counters["d"] += 1
        return int(c.node_outputs["b"]) + int(c.node_outputs["c"])

    wf = DynamicWorkflow(name="dag")
    wf.add_node("a", handler=_step_a)
    wf.add_node("b", handler=_step_b, depends_on=["a"])
    wf.add_node("c", handler=_step_c, depends_on=["a"])
    wf.add_node("d", handler=_step_d, depends_on=["b", "c"])

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("dag")
        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        run, ctx = await engine.snapshot(run_id)
        assert run.status == "completed"
        assert ctx.node_outputs == {"a": 10, "b": 20, "c": 15, "d": 35}
        assert counters == {"a": 1, "b": 1, "c": 1, "d": 1}


@pytest.mark.anyio
async def test_duplicate_advance_is_idempotent() -> None:
    """Calling advance multiple times concurrently does not re-execute nodes."""
    counter = {"a": 0}

    def _step_a(c: WorkflowContext) -> str:
        counter["a"] += 1
        return "done"

    wf = DynamicWorkflow(name="single")
    wf.add_node("a", handler=_step_a)

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("single")
        await engine.advance(run_id)
        await engine.advance(run_id)
        await engine.advance(run_id)

        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        run, _ctx = await engine.snapshot(run_id)
        assert run.status == "completed"
        assert counter["a"] == 1


@pytest.mark.anyio
async def test_failed_node_skips_dependents_and_fails_run() -> None:
    """Failed node causes dependents to skip and marks the run failed."""

    def _step_b(c: WorkflowContext) -> None:
        msg = "boom"
        raise ValueError(msg)

    wf = DynamicWorkflow(name="fail_wf")
    wf.add_node("a", handler=lambda _c: "ok")
    wf.add_node("b", handler=_step_b, depends_on=["a"])
    wf.add_node("d", handler=lambda _c: "never", depends_on=["b"])

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("fail_wf")
        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        run, ctx = await engine.snapshot(run_id)
        assert run.status == "failed"
        assert run.error is not None
        assert "ValueError: boom" in run.error
        assert ctx.step_results["b"].status == "failed"
        assert ctx.step_results["d"].status == "skipped"


@pytest.mark.anyio
async def test_condition_false_skips() -> None:
    """Node with condition evaluating to False is marked skipped."""
    wf = DynamicWorkflow(name="cond_wf")
    wf.add_node("root", handler=lambda _c: "start")
    wf.add_node(
        WorkflowNode(
            name="optional",
            handler=lambda _c: "not executed",
            depends_on=["root"],
            condition=lambda _c: False,
        )
    )
    wf.add_node("final", handler=lambda _c: "end", depends_on=["optional"])

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("cond_wf")
        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        run, ctx = await engine.snapshot(run_id)
        assert run.status == "completed"
        assert ctx.step_results["optional"].status == "skipped"
        assert ctx.step_results["final"].status == "skipped"


@pytest.mark.anyio
async def test_agent_node_uses_upstream_output_and_unique_session() -> None:
    """Agent node receives joined upstream outputs and uses scoped session ID."""
    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="agent output")]])
    agent = Agent(name="worker_agent", model=model)

    wf = DynamicWorkflow(name="agent_wf")
    wf.add_node("step1", handler=lambda _c: "upstream data")
    wf.add_node("agent_step", agent=agent, depends_on=["step1"])

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("agent_wf")
        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        run, ctx = await engine.snapshot(run_id)
        assert run.status == "completed"
        assert ctx.node_outputs["agent_step"] == "agent output"
        node_rec = await engine.store.get_node(run_id, "agent_step")
        assert node_rec is not None
        assert node_rec.output == "agent output"


@pytest.mark.anyio
async def test_redelivered_node_does_not_rerun() -> None:
    """A redelivered node whose record already exists returns immediately."""
    counter = {"a": 0}

    def _step_a(c: WorkflowContext) -> str:
        counter["a"] += 1
        return "val"

    wf = DynamicWorkflow(name="redeliver")
    wf.add_node("a", handler=_step_a)

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("redeliver")
        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        assert counter["a"] == 1
        await execute_workflow_node(run_id=run_id, node="a", workflow_engine=engine)
        await execute_workflow_node(run_id=run_id, node="a", workflow_engine=engine)
        assert counter["a"] == 1


@pytest.mark.anyio
async def test_unserializable_output_fails_node() -> None:
    """Node producing non-JSON-serializable output is marked failed."""
    wf = DynamicWorkflow(name="unserializable")
    wf.add_node("bad_node", handler=lambda _c: object())

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("unserializable")
        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        run, ctx = await engine.snapshot(run_id)
        assert run.status == "failed"
        assert ctx.step_results["bad_node"].status == "failed"
        assert "not JSON serializable" in str(ctx.step_results["bad_node"].error)


@pytest.mark.anyio
async def test_reservations_released_on_terminal() -> None:
    """Forever node reservations are released after workflow reaches terminal state."""
    wf = DynamicWorkflow(name="res_wf")
    wf.add_node("step1", handler=lambda _c: "ok")

    app, engine, plugin = _make_engine_and_app([wf])
    async with AsyncTestClient(app):
        run_id = await engine.start("res_wf")
        service = plugin.config.get_service(app.state)
        await _drain(service, plugin.config.worker, engine, run_id)

        run, _ = await engine.snapshot(run_id)
        assert run.status == "completed"
        identity = await service.get_task_identity(f"wf-node:{run_id}:step1")
        assert identity is None


@pytest.mark.anyio
async def test_start_without_queue_plugin_raises() -> None:
    """Starting an engine without an active QueuePlugin raises ImproperlyConfiguredException."""
    wf = DynamicWorkflow(name="no_plugin")
    wf.add_node("step1", handler=lambda _c: "ok")
    registry = WorkflowRegistry([wf])
    engine = DurableWorkflowEngine(registry=registry, store=WorkflowStore(MemoryStore()))

    with pytest.raises(ImproperlyConfiguredException, match="requires QueuePlugin"):
        await engine.start("no_plugin")


def test_workflow_registry_validates_and_rejects_duplicates() -> None:
    """WorkflowRegistry validates DAGs and enforces unique workflow names."""
    wf1 = DynamicWorkflow(name="dup")
    wf1.add_node("n1", handler=lambda _c: 1)
    wf2 = DynamicWorkflow(name="dup")
    wf2.add_node("n1", handler=lambda _c: 2)

    with pytest.raises(ImproperlyConfiguredException, match="Duplicate workflow name"):
        WorkflowRegistry([wf1, wf2])

    registry = WorkflowRegistry([wf1])
    with pytest.raises(KeyError, match="Unknown workflow"):
        registry.get("nonexistent")


@pytest.mark.anyio
async def test_watchdog_reenqueues_stalled_advance() -> None:
    """A run whose advance was lost completes after a watchdog tick."""
    counter = {"a": 0, "b": 0}

    def _step_a(_c: WorkflowContext) -> str:
        counter["a"] += 1
        return "a_val"

    def _step_b(c: WorkflowContext) -> str:
        counter["b"] += 1
        return f"b_{c.node_outputs['a']}"

    wf = DynamicWorkflow(name="stalled_wf")
    wf.add_node("a", handler=_step_a)
    wf.add_node("b", handler=_step_b, depends_on=["a"])

    app, engine, plugin = _make_engine_and_app([wf], watchdog_interval=None)
    async with AsyncTestClient(app):
        service = plugin.config.get_service(app.state)
        run_id = await engine.start("stalled_wf")
        backend = service.get_queue_backend()
        await backend.cancel_tasks(queue="workflow")

        await engine.store.put_node(run_id, NodeRecord(node="a", status="completed", output="a_val"))
        await service.reset_task_identity(f"wf-node:{run_id}:a")

        status = await watch_workflow(run_id=run_id, tick=1, workflow_engine=engine)
        assert status == "running"

        await _drain(service, plugin.config.worker, engine, run_id)
        run, ctx = await engine.snapshot(run_id)
        assert run.status == "completed"
        assert counter["a"] == 0
        assert counter["b"] == 1
        assert ctx.node_outputs["b"] == "b_a_val"


@pytest.mark.anyio
async def test_watchdog_schedules_next_tick() -> None:
    """Watchdog schedules the next tick with run_after when run is still running."""
    counter = {"steps": 0}

    async def _long_step(_c: WorkflowContext) -> str:
        counter["steps"] += 1
        return "step_done"

    wf = DynamicWorkflow(name="long_wf")
    wf.add_node("step1", handler=_long_step)

    app, engine, plugin = _make_engine_and_app([wf], watchdog_interval=60.0)
    async with AsyncTestClient(app):
        service = plugin.config.get_service(app.state)
        run_id = await engine.start("long_wf")

        backend = service.get_queue_backend()
        tick1_task = await backend.get_task_by_key(f"wf-watchdog:{run_id}:1")
        assert tick1_task is not None
        assert tick1_task.scheduled_at is not None
        now = datetime.now(timezone.utc)
        diff1 = abs((tick1_task.scheduled_at - (now + timedelta(seconds=60))).total_seconds())
        assert diff1 < 5.0

        status = await watch_workflow(run_id=run_id, tick=1, workflow_engine=engine)
        assert status == "running"

        tick2_task = await backend.get_task_by_key(f"wf-watchdog:{run_id}:2")
        assert tick2_task is not None
        assert tick2_task.scheduled_at is not None
        diff2 = abs((tick2_task.scheduled_at - (now + timedelta(seconds=60))).total_seconds())
        assert diff2 < 5.0


@pytest.mark.anyio
async def test_watchdog_stops_on_terminal_run() -> None:
    """Watchdog tick returns terminal status and does not enqueue another tick."""
    wf = DynamicWorkflow(name="quick_wf")
    wf.add_node("n1", handler=lambda _c: "quick")

    app, engine, plugin = _make_engine_and_app([wf], watchdog_interval=30.0)
    async with AsyncTestClient(app):
        service = plugin.config.get_service(app.state)
        run_id = await engine.start("quick_wf")
        await _drain(service, plugin.config.worker, engine, run_id)

        run, _ = await engine.snapshot(run_id)
        assert run.status == "completed"

        assert await watch_workflow(run_id="nonexistent", tick=1, workflow_engine=engine) is None

        tick_result = await watch_workflow(run_id=run_id, tick=2, workflow_engine=engine)
        assert tick_result == "completed"

        backend = service.get_queue_backend()
        tick3_task = await backend.get_task_by_key(f"wf-watchdog:{run_id}:3")
        assert tick3_task is None


@pytest.mark.anyio
async def test_watchdog_times_out_old_run() -> None:
    """Watchdog marks runs older than run_timeout_seconds as failed and releases reservations."""
    wf = DynamicWorkflow(name="timeout_wf")
    wf.add_node("slow_node", handler=lambda _c: "ok")

    app, engine, plugin = _make_engine_and_app([wf], watchdog_interval=30.0, run_timeout_seconds=0.0)
    async with AsyncTestClient(app):
        service = plugin.config.get_service(app.state)
        run_id = await engine.start("timeout_wf")

        status = await watch_workflow(run_id=run_id, tick=1, workflow_engine=engine)
        assert status == "failed"

        run, _ = await engine.snapshot(run_id)
        assert run.status == "failed"
        assert run.error == "workflow timed out"

        identity = await service.get_task_identity(f"wf-node:{run_id}:slow_node")
        assert identity is None


@pytest.mark.anyio
async def test_watchdog_immediate_backend_behavior() -> None:
    """Immediate backend rejects start when watchdog is enabled, but succeeds when disabled."""
    wf = DynamicWorkflow(name="imm_wf")
    wf.add_node("step1", handler=lambda _c: "done")

    app_rejected, engine_rejected, _ = _make_engine_and_app(
        [wf],
        watchdog_interval=30.0,
        execution_backend="immediate",
    )
    async with AsyncTestClient(app_rejected):
        with pytest.raises(ImproperlyConfiguredException, match="watchdog requires a non-immediate execution backend"):
            await engine_rejected.start("imm_wf")

    app_allowed, engine_allowed, _ = _make_engine_and_app(
        [wf],
        watchdog_interval=None,
        execution_backend="immediate",
    )
    async with AsyncTestClient(app_allowed):
        run_id = await engine_allowed.start("imm_wf")
        assert run_id is not None
        run, ctx = await engine_allowed.snapshot(run_id)
        assert run.status == "completed"
        assert ctx.node_outputs["step1"] == "done"
