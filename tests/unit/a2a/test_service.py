"""Unit tests for A2A handler service, task context, and task store."""

import pytest
from litestar import Litestar

from litestar_mcp.a2a.context import TaskContext
from litestar_mcp.a2a.registry import A2ARegistry, SkillRegistration
from litestar_mcp.a2a.service import A2AHandlerService
from litestar_mcp.a2a.tasks import A2AMemoryTaskStore, A2ATaskStore
from litestar_mcp.a2a.types import Artifact, Task, TextPart
from litestar_mcp.core import JSONRPCRequest


@pytest.fixture
def app() -> Litestar:
    """Minimal Litestar app for synthetic pipeline execution."""
    return Litestar(route_handlers=[])


@pytest.fixture
def task_store() -> A2ATaskStore:
    """In-memory A2A task store."""
    return A2AMemoryTaskStore(default_ttl_ms=60_000)


@pytest.fixture
def registry() -> A2ARegistry:
    """A2A skill registry populated with test handlers."""
    reg = A2ARegistry()

    def sync_calc(a: int, b: int) -> dict[str, int]:
        """Perform addition."""
        return {"result": a + b}

    async def async_agent_skill(query: str, context: TaskContext) -> str:
        """Run multi-step reasoning."""
        await context.thought("Analyzing request...")
        await context.report_status("working", message="Step 1 complete")
        await context.emit_artifact(Artifact(name="summary.txt", parts=[TextPart(text="Done summary")]))
        return f"Processed: {query}"

    reg.register(SkillRegistration(fn=sync_calc, id="calc", name="Calculator"))
    reg.register(SkillRegistration(fn=async_agent_skill, id="agent_skill", name="Agent Skill"))
    return reg


@pytest.fixture
def service(app: Litestar, registry: A2ARegistry, task_store: A2ATaskStore) -> A2AHandlerService:
    """Configured A2AHandlerService instance."""
    return A2AHandlerService(app=app, registry=registry, task_store=task_store)


@pytest.mark.asyncio
async def test_task_store_crud(task_store: A2ATaskStore) -> None:
    """Test task store save, get, and update."""
    task = Task(id="t-1", session_id="s-1")
    await task_store.save_task(task)

    retrieved = await task_store.get_task("t-1")
    assert retrieved.id == "t-1"
    assert retrieved.session_id == "s-1"
    assert retrieved.status.state == "submitted"

    # Update status
    await task_store.update_task_status("t-1", "working", message="in flight")
    updated = await task_store.get_task("t-1")
    assert updated.status.state == "working"
    assert updated.status.message == "in flight"


@pytest.mark.asyncio
async def test_service_tasks_send_sync(service: A2AHandlerService) -> None:
    """Test tasks/send executing synchronous skill returns immediate completed Task."""
    req = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        id=1,
        params={
            "skill": "calc",
            "message": {
                "role": "user",
                "parts": [{"type": "data", "data": {"a": 10, "b": 20}}],
            },
        },
    )

    response = await service.dispatch_request(req)
    assert response is not None
    result = response["result"]
    assert result["status"]["state"] == "completed"
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0]["parts"][0]["data"] == {"result": 30}


@pytest.mark.asyncio
async def test_service_tasks_send_with_task_context(service: A2AHandlerService, task_store: A2ATaskStore) -> None:
    """Test tasks/send with TaskContext thoughts and emitted artifacts."""
    req = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        id=2,
        params={
            "skill": "agent_skill",
            "message": {
                "role": "user",
                "parts": [{"type": "data", "data": {"query": "test query"}}],
            },
        },
    )

    response = await service.dispatch_request(req)
    assert response is not None
    result = response["result"]
    assert result["status"]["state"] == "completed"
    assert len(result["artifacts"]) >= 2

    # Check persistence in store
    task_id = result["id"]
    persisted = await task_store.get_task(task_id)
    assert persisted.status.state == "completed"


@pytest.mark.asyncio
async def test_service_tasks_get_and_cancel(service: A2AHandlerService, task_store: A2ATaskStore) -> None:
    """Test tasks/get and tasks/cancel lifecycle endpoints."""
    task = Task(id="t-cancel-1", session_id="s-1")
    await task_store.save_task(task)

    # tasks/get
    get_req = JSONRPCRequest(jsonrpc="2.0", method="tasks/get", id=3, params={"id": "t-cancel-1"})
    get_resp = await service.dispatch_request(get_req)
    assert get_resp is not None
    assert get_resp["result"]["id"] == "t-cancel-1"

    # tasks/cancel
    cancel_req = JSONRPCRequest(jsonrpc="2.0", method="tasks/cancel", id=4, params={"id": "t-cancel-1"})
    cancel_resp = await service.dispatch_request(cancel_req)
    assert cancel_resp is not None
    assert cancel_resp["result"]["status"]["state"] == "canceled"

    # Verify updated in store
    cancelled_task = await task_store.get_task("t-cancel-1")
    assert cancelled_task.status.state == "canceled"
