"""Unit tests for WorkflowStore and durable run models."""

from datetime import timedelta

import pytest
from litestar.stores.memory import MemoryStore

from litestar_mcp.agent.workflow import StepResult, WorkflowContext
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
