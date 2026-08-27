"""Unit tests for A2A SSE real-time streaming adapter and subscription manager."""

from typing import Any

import pytest
from litestar import Litestar

from litestar_mcp.a2a.context import TaskContext
from litestar_mcp.a2a.registry import A2ARegistry, SkillRegistration
from litestar_mcp.a2a.service import A2AHandlerService
from litestar_mcp.a2a.streaming import A2ASubscriptionManager, format_a2a_sse_event
from litestar_mcp.a2a.tasks import A2AMemoryTaskStore
from litestar_mcp.a2a.types import (
    Artifact,
    DataPart,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from litestar_mcp.core import JSONRPCRequest


def test_format_a2a_sse_event() -> None:
    """Verify A2A SSE wire format packages events inside standard JSON-RPC envelopes."""
    status_event = TaskStatusUpdateEvent(
        task_id="t-1",
        status=TaskStatus(state="working", message="Running step"),
        final=False,
    )
    payload = format_a2a_sse_event(status_event)
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "tasks/statusUpdate"
    assert payload["params"]["taskId"] == "t-1"
    assert payload["params"]["status"]["state"] == "working"

    art_event = TaskArtifactUpdateEvent(
        task_id="t-1",
        artifact=Artifact(name="result.json", parts=[DataPart(data={"ok": True})]),
        final=True,
    )
    art_payload = format_a2a_sse_event(art_event)
    assert art_payload["jsonrpc"] == "2.0"
    assert art_payload["method"] == "tasks/artifactUpdate"
    assert art_payload["params"]["taskId"] == "t-1"
    assert art_payload["params"]["final"] is True


@pytest.mark.asyncio
async def test_a2a_subscription_manager_lifecycle() -> None:
    """Test A2ASubscriptionManager streaming queue and broadcasting."""
    manager = A2ASubscriptionManager(max_streams=5)
    sub_id, stream = await manager.open_a2a_stream("task-123")

    status_event = TaskStatusUpdateEvent(
        task_id="task-123",
        status=TaskStatus(state="working", message="Step 1"),
        final=False,
    )
    await manager.publish_event(status_event)

    msg = await anext(stream)
    assert msg["method"] == "tasks/statusUpdate"
    assert msg["params"]["taskId"] == "task-123"

    await manager.disconnect(sub_id)


@pytest.mark.asyncio
async def test_service_streaming_send_subscribe() -> None:
    """Test A2AHandlerService tasks/sendSubscribe streaming generator."""
    app = Litestar(route_handlers=[])
    registry = A2ARegistry()
    task_store = A2AMemoryTaskStore()
    sub_manager = A2ASubscriptionManager()

    async def stream_skill(prompt: str, context: TaskContext) -> None:
        await context.thought("Analyzing prompt...")
        await context.emit_artifact(Artifact(name="chunk1", parts=[TextPart(text="Chunk 1")]))
        await context.emit_artifact(Artifact(name="chunk2", parts=[TextPart(text="Chunk 2")]), final=True)
        await context.report_status("completed")

    registry.register(SkillRegistration(fn=stream_skill, id="stream_skill", name="Streaming Skill"))

    service = A2AHandlerService(
        app=app,
        registry=registry,
        task_store=task_store,
        subscription_manager=sub_manager,
    )

    req = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/sendSubscribe",
        id=10,
        params={
            "skill": "stream_skill",
            "message": {
                "role": "user",
                "parts": [{"type": "data", "data": {"prompt": "generate story"}}],
            },
        },
    )

    events: list[dict[str, Any]] = [event async for event in service.stream_tasks_send_subscribe(req)]

    assert len(events) >= 3
    methods = [e["method"] for e in events]
    assert "tasks/statusUpdate" in methods
    assert "tasks/artifactUpdate" in methods
