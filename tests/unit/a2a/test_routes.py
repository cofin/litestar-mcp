"""Unit tests for A2A JSON-RPC route handlers and streaming endpoints."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.context import TaskContext
from litestar_mcp.a2a.plugin import A2APlugin
from litestar_mcp.a2a.types import Artifact, TextPart


@get("/skills/multiply", opt={"a2a_skill": "multiply"})
def multiply(x: int, y: int) -> dict[str, int]:
    """Multiply two integers."""
    return {"result": x * y}


@get("/skills/stream_task", opt={"a2a_skill": "stream_task"})
async def stream_task(text: str, context: TaskContext) -> str:
    """Stream progress and emit an artifact."""
    await context.thought("Computing...")
    await context.emit_artifact(Artifact(name="output.txt", parts=[TextPart(text=f"Processed: {text}")]))
    return f"Done: {text}"


@pytest.fixture
def client() -> Any:
    """Test client configured with A2APlugin and test skills."""
    plugin = A2APlugin(config=A2AConfig(name="Route Test Agent"))
    app = Litestar(route_handlers=[multiply, stream_task], plugins=[plugin])
    with TestClient(app=app) as c:
        yield c


def test_post_jsonrpc_tasks_send_success(client: Any) -> None:
    """Test POST /a2a with tasks/send returns completed task result."""
    payload = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tasks/send",
        "params": {
            "skill": "multiply",
            "message": {
                "role": "user",
                "parts": [{"type": "data", "data": {"x": 6, "y": 7}}],
            },
        },
    }
    resp = client.post("/a2a", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 100
    assert "result" in data
    assert data["result"]["status"]["state"] == "completed"
    assert data["result"]["artifacts"][0]["parts"][0]["data"] == {"result": 42}


def test_post_jsonrpc_tasks_get_and_cancel(client: Any) -> None:
    """Test POST /a2a tasks/get and tasks/cancel lifecycle."""
    send_payload = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tasks/send",
        "params": {
            "skill": "multiply",
            "message": {"role": "user", "parts": [{"type": "data", "data": {"x": 2, "y": 3}}]},
        },
    }
    send_resp = client.post("/a2a", json=send_payload)
    task_id = send_resp.json()["result"]["id"]

    get_payload = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tasks/get",
        "params": {"id": task_id},
    }
    get_resp = client.post("/a2a", json=get_payload)
    assert get_resp.status_code == 200
    assert get_resp.json()["result"]["id"] == task_id

    cancel_payload = {
        "jsonrpc": "2.0",
        "id": 103,
        "method": "tasks/cancel",
        "params": {"id": task_id},
    }
    cancel_resp = client.post("/a2a", json=cancel_payload)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["result"]["status"]["state"] == "canceled"


def test_post_jsonrpc_invalid_method(client: Any) -> None:
    """Test POST /a2a with unknown method returns JSON-RPC method not found error."""
    payload = {
        "jsonrpc": "2.0",
        "id": 999,
        "method": "nonexistent/method",
        "params": {},
    }
    resp = client.post("/a2a", json=payload)
    assert resp.status_code in (200, 404)
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32601


def test_post_jsonrpc_parse_error(client: Any) -> None:
    """Test POST /a2a with malformed JSON body returns parse error."""
    resp = client.post("/a2a", content=b"invalid json")
    assert resp.status_code in (200, 400)
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32700


def test_post_jsonrpc_stream_send_subscribe(client: Any) -> None:
    """Test tasks/sendSubscribe streaming SSE endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "id": 200,
        "method": "tasks/sendSubscribe",
        "params": {
            "skill": "stream_task",
            "message": {
                "role": "user",
                "parts": [{"type": "data", "data": {"text": "hello stream"}}],
            },
        },
    }
    resp = client.post("/a2a", json=payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert "tasks/statusUpdate" in resp.text
