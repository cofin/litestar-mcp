"""End-to-end integration tests for the A2A protocol."""

import pytest
from litestar import Litestar, get, post
from litestar.testing import AsyncTestClient

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.context import TaskContext
from litestar_mcp.a2a.plugin import A2APlugin


@get("/skills/echo", opt={"a2a_skill": "echo", "a2a_description": "Echo input message"})
def echo_handler(message: str) -> dict[str, str]:
    """Echo handler for A2A."""
    return {"reply": message}


@post("/skills/compute", opt={"a2a_skill": "compute", "a2a_description": "Compute sum"})
async def compute_handler(a: int, b: int, ctx: TaskContext) -> dict[str, int]:
    """Compute handler updating status in task context."""
    await ctx.report_status(state="working", message="Calculating...")
    return {"sum": a + b}


@pytest.fixture
def a2a_app() -> Litestar:
    """Create a Litestar test application with A2APlugin."""
    config = A2AConfig(
        name="E2E Test Agent",
        description="Comprehensive integration test agent",
        version="2.0.0",
        base_path="/a2a",
    )
    plugin = A2APlugin(config=config)
    return Litestar(
        route_handlers=[echo_handler, compute_handler],
        plugins=[plugin],
    )


@pytest.mark.anyio
async def test_a2a_agent_card_endpoint(a2a_app: Litestar) -> None:
    """Test GET /.well-known/agent-card.json returns valid card manifest."""
    async with AsyncTestClient(app=a2a_app) as client:
        response = await client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
        card = response.json()
        assert card["name"] == "E2E Test Agent"
        assert card["version"] == "2.0.0"
        assert card["protocolVersion"] == "1.0"
        skill_ids = [s["id"] for s in card["skills"]]
        assert "echo" in skill_ids
        assert "compute" in skill_ids


@pytest.mark.anyio
async def test_a2a_tasks_send_and_get_lifecycle(a2a_app: Litestar) -> None:
    """Test sending a task, verifying execution, and retrieving task state."""
    async with AsyncTestClient(app=a2a_app) as client:
        send_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tasks/send",
            "params": {
                "skill": "echo",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hello a2a world"}],
                },
            },
        }
        send_resp = await client.post("/a2a", json=send_payload)
        assert send_resp.status_code == 200
        result = send_resp.json()["result"]
        assert result["status"]["state"] == "completed"
        task_id = result["id"]

        get_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        get_resp = await client.post("/a2a", json=get_payload)
        assert get_resp.status_code == 200
        fetched = get_resp.json()["result"]
        assert fetched["id"] == task_id
        assert fetched["status"]["state"] == "completed"


@pytest.mark.anyio
async def test_a2a_tasks_cancel(a2a_app: Litestar) -> None:
    """Test cancelling an existing task via tasks/cancel."""
    async with AsyncTestClient(app=a2a_app) as client:
        send_payload = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tasks/send",
            "params": {
                "skill": "echo",
                "message": {"role": "user", "parts": [{"type": "text", "text": "test cancel"}]},
            },
        }
        send_resp = await client.post("/a2a", json=send_payload)
        task_id = send_resp.json()["result"]["id"]

        cancel_payload = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        cancel_resp = await client.post("/a2a", json=cancel_payload)
        assert cancel_resp.status_code == 200
        result = cancel_resp.json()["result"]
        assert result["id"] == task_id
        assert result["status"]["state"] == "canceled"


@pytest.mark.anyio
async def test_a2a_sse_streaming(a2a_app: Litestar) -> None:
    """Test real-time SSE streaming updates via tasks/sendSubscribe."""
    async with AsyncTestClient(app=a2a_app) as client:
        subscribe_payload = {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tasks/sendSubscribe",
            "params": {
                "skill": "compute",
                "message": {
                    "role": "user",
                    "parts": [{"type": "data", "data": {"a": 10, "b": 25}}],
                },
            },
        }
        response = await client.post("/a2a", json=subscribe_payload)
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        content = response.text
        assert "tasks/statusUpdate" in content
        assert "completed" in content


@pytest.mark.anyio
async def test_a2a_error_handling(a2a_app: Litestar) -> None:
    """Test JSON-RPC error formats for invalid methods and parameters."""
    async with AsyncTestClient(app=a2a_app) as client:
        unknown_resp = await client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 99, "method": "unknown/method"},
        )
        assert unknown_resp.status_code == 200
        data = unknown_resp.json()
        assert data["error"]["code"] == -32601
        assert "not found" in data["error"]["message"].lower()

        missing_skill_resp = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tasks/send",
                "params": {"skill": "non_existent"},
            },
        )
        assert missing_skill_resp.status_code == 200
        missing_data = missing_skill_resp.json()
        assert missing_data["error"]["code"] == -32601
