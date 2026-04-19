"""Tests for experimental MCP task support."""

import json
import time
from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.utils import mcp_tool


def _ensure_session(client: TestClient[Any]) -> str:
    sid = getattr(client, "_mcp_session", None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    if sid:
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": sid},
        )
    client._mcp_session = sid  # type: ignore[attr-defined]
    return str(sid)


def _rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    headers: "dict[str, str] | None" = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    final_headers = dict(headers or {})
    if method != "initialize" and "Mcp-Session-Id" not in final_headers and "mcp-session-id" not in final_headers:
        sid = _ensure_session(client)
        if sid:
            final_headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=final_headers).json()  # type: ignore[no-any-return]


def _make_task_app() -> Litestar:
    @get("/optional-task", sync_to_thread=False)
    @mcp_tool(name="optional_task", task_support="optional")
    async def optional_task(delay: float = 0.01) -> dict[str, str]:
        import asyncio

        await asyncio.sleep(delay)
        return {"status": "completed"}

    @get("/required-task", sync_to_thread=False)
    @mcp_tool(name="required_task", task_support="required")
    async def required_task(delay: float = 0.01) -> dict[str, str]:
        import asyncio

        await asyncio.sleep(delay)
        return {"status": "completed"}

    @get("/forbidden-task", sync_to_thread=False)
    @mcp_tool(name="forbidden_task", task_support="forbidden")
    async def forbidden_task(delay: float = 0.01) -> dict[str, str]:
        import asyncio

        await asyncio.sleep(delay)
        return {"status": "completed"}

    @get("/cancel-task", sync_to_thread=False)
    @mcp_tool(name="cancel_task", task_support="optional")
    async def cancel_task(delay: float = 0.5) -> dict[str, str]:
        import asyncio

        await asyncio.sleep(delay)
        return {"status": "completed"}

    return Litestar(
        route_handlers=[optional_task, required_task, forbidden_task, cancel_task],
        plugins=[LitestarMCP(MCPConfig(tasks=True))],
    )


def test_initialize_advertises_task_capabilities() -> None:
    app = _make_task_app()
    with TestClient(app=app) as client:
        result = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        assert result["result"]["capabilities"]["tasks"]["list"] == {}
        assert result["result"]["capabilities"]["tasks"]["cancel"] == {}
        assert result["result"]["capabilities"]["tasks"]["requests"]["tools"]["call"] == {}


def test_tools_list_includes_task_support_metadata() -> None:
    app = _make_task_app()
    with TestClient(app=app) as client:
        result = _rpc(client, "tools/list")
        tools = {tool["name"]: tool for tool in result["result"]["tools"]}

        assert tools["optional_task"]["execution"]["taskSupport"] == "optional"
        assert tools["required_task"]["execution"]["taskSupport"] == "required"
        assert tools["forbidden_task"]["execution"]["taskSupport"] == "forbidden"


def test_optional_tool_can_be_executed_as_task() -> None:
    app = _make_task_app()
    headers = {"x-mcp-client-id": "task-client-1"}
    with TestClient(app=app) as client:
        result = _rpc(
            client,
            "tools/call",
            {
                "name": "optional_task",
                "arguments": {"delay": 0.01},
                "task": {"ttl": 1000},
            },
            headers=headers,
        )

        assert "task" in result["result"]
        task_id = result["result"]["task"]["taskId"]
        time.sleep(0.05)

        task_result = _rpc(client, "tasks/result", {"taskId": task_id}, headers=headers)
        payload = task_result["result"]
        parsed = json.loads(payload["content"][0]["text"])

        assert payload["_meta"]["io.modelcontextprotocol/related-task"]["taskId"] == task_id
        assert parsed["status"] == "completed"


def test_required_task_support_rejects_sync_call() -> None:
    app = _make_task_app()
    with TestClient(app=app) as client:
        result = _rpc(client, "tools/call", {"name": "required_task", "arguments": {"delay": 0.01}})
        assert result["error"]["code"] == -32600


def test_forbidden_task_support_rejects_task_augmentation() -> None:
    app = _make_task_app()
    with TestClient(app=app) as client:
        result = _rpc(
            client,
            "tools/call",
            {
                "name": "forbidden_task",
                "arguments": {"delay": 0.01},
                "task": {"ttl": 1000},
            },
        )
        assert result["error"]["code"] == -32601


def test_tasks_cancel_marks_task_cancelled() -> None:
    app = _make_task_app()
    headers = {"x-mcp-client-id": "task-client-2"}
    with TestClient(app=app) as client:
        result = _rpc(
            client,
            "tools/call",
            {
                "name": "cancel_task",
                "arguments": {"delay": 0.5},
                "task": {"ttl": 1000},
            },
            headers=headers,
        )
        task_id = result["result"]["task"]["taskId"]

        cancelled = _rpc(client, "tasks/cancel", {"taskId": task_id}, headers=headers)
        assert cancelled["result"]["status"] == "cancelled"

        listed = _rpc(client, "tasks/list", {}, headers=headers)
        task_ids = [task["taskId"] for task in listed["result"]["tasks"]]
        assert task_id in task_ids
