"""MCP 2026-07-28 Tasks extension tests."""

import time
from typing import Any, cast

from litestar import Litestar, get
from litestar.stores.memory import MemoryStore
from litestar.testing import TestClient

from litestar_mcp import (
    LitestarMCP,
    MCPConfig,
    MCPInputRequiredResult,
    MCPTaskConfig,
    get_mcp_request_context,
)
from litestar_mcp.utils import mcp_tool

PROTOCOL_VERSION = "2026-07-28"
TASKS_EXTENSION = "io.modelcontextprotocol/tasks"


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    tasks_capable: bool = False,
) -> dict[str, Any]:
    request_params = dict(params or {})
    capabilities: dict[str, Any] = {}
    if tasks_capable:
        capabilities["extensions"] = {TASKS_EXTENSION: {}}
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": capabilities,
        "io.modelcontextprotocol/clientInfo": {"name": "tasks-tests", "version": "1"},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    name_field = {
        "tools/call": "name",
        "tasks/get": "taskId",
        "tasks/update": "taskId",
        "tasks/cancel": "taskId",
    }.get(method)
    if name_field is not None:
        headers["Mcp-Name"] = str(request_params.get(name_field, ""))
    return cast(
        "dict[str, Any]",
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": request_params},
            headers=headers,
        ).json(),
    )


def _make_task_app(task_config: MCPTaskConfig | None = None) -> Litestar:
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
    async def forbidden_task() -> dict[str, str]:
        return {"status": "sync"}

    @get("/input-task", sync_to_thread=False)
    @mcp_tool(name="input_task", task_support="optional")
    async def input_task() -> MCPInputRequiredResult | dict[str, str]:
        context = get_mcp_request_context()
        if not context.input_responses:
            return MCPInputRequiredResult(
                input_requests={"approval": {"method": "elicitation/create", "params": {"message": "Approve?"}}},
                request_state="signed-or-encrypted-opaque-state",
            )
        return {"answer": str(context.input_responses["approval"])}

    return Litestar(
        route_handlers=[optional_task, required_task, forbidden_task, input_task],
        plugins=[LitestarMCP(MCPConfig(tasks=task_config or True))],
    )


def _wait_for_status(client: TestClient[Any], task_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        result = _rpc(client, "tasks/get", {"taskId": task_id}, tasks_capable=True)["result"]
        if result["status"] == status:
            return cast("dict[str, Any]", result)
        time.sleep(0.01)
    msg = f"task {task_id} did not reach {status}"
    raise AssertionError(msg)


def test_discovery_advertises_only_enabled_tasks_extension() -> None:
    with TestClient(app=_make_task_app()) as client:
        enabled = _rpc(client, "server/discover")["result"]
    with TestClient(app=Litestar(plugins=[LitestarMCP()])) as client:
        disabled = _rpc(client, "server/discover")["result"]

    assert enabled["capabilities"]["extensions"] == {TASKS_EXTENSION: {}}
    assert "extensions" not in disabled["capabilities"]


def test_task_support_is_server_policy_not_tool_wire_metadata() -> None:
    with TestClient(app=_make_task_app()) as client:
        tools = _rpc(client, "tools/list")["result"]["tools"]

    assert all("execution" not in tool for tool in tools)


def test_optional_tool_uses_task_for_capable_client_and_sync_fallback_otherwise() -> None:
    with TestClient(app=_make_task_app()) as client:
        synchronous = _rpc(
            client,
            "tools/call",
            {"name": "optional_task", "arguments": {"delay": 0}},
        )["result"]
        created = _rpc(
            client,
            "tools/call",
            {"name": "optional_task", "arguments": {"delay": 0.01}},
            tasks_capable=True,
        )["result"]
        completed = _wait_for_status(client, created["taskId"], "completed")

    assert synchronous["resultType"] == "complete"
    assert created["resultType"] == "task"
    assert created["ttlMs"] == 300_000
    assert created["pollIntervalMs"] == 1_000
    assert completed["result"]["isError"] is False


def test_required_tool_rejects_client_without_extension() -> None:
    with TestClient(app=_make_task_app()) as client:
        response = _rpc(client, "tools/call", {"name": "required_task", "arguments": {"delay": 0}})

    assert response["error"]["code"] == -32021


def test_tool_can_require_a_standard_client_capability() -> None:
    @get(
        "/sampling",
        mcp_tool="sampling_tool",
        mcp_required_client_capabilities={"sampling": {}},
        sync_to_thread=False,
    )
    def sampling_tool() -> str:
        return "sampled"

    with TestClient(app=Litestar(route_handlers=[sampling_tool], plugins=[LitestarMCP()])) as client:
        missing = _rpc(client, "tools/call", {"name": "sampling_tool", "arguments": {}})

    assert missing["error"]["code"] == -32021
    assert missing["error"]["data"]["requiredCapabilities"] == {"sampling": {}}


def test_task_methods_reject_requests_without_extension_capability() -> None:
    with TestClient(app=_make_task_app()) as client:
        response = _rpc(client, "tasks/get", {"taskId": "opaque-task-id"})

    assert response["error"]["code"] == -32021


def test_forbidden_tool_remains_synchronous_for_capable_client() -> None:
    with TestClient(app=_make_task_app()) as client:
        result = _rpc(
            client,
            "tools/call",
            {"name": "forbidden_task", "arguments": {}},
            tasks_capable=True,
        )["result"]

    assert result["resultType"] == "complete"


def test_tasks_update_resumes_input_required_task() -> None:
    with TestClient(app=_make_task_app()) as client:
        created = _rpc(
            client,
            "tools/call",
            {"name": "input_task", "arguments": {}},
            tasks_capable=True,
        )["result"]
        waiting = _wait_for_status(client, created["taskId"], "input_required")
        updated = _rpc(
            client,
            "tasks/update",
            {"taskId": created["taskId"], "inputResponses": {"approval": True, "unknown": "ignored"}},
            tasks_capable=True,
        )
        completed = _wait_for_status(client, created["taskId"], "completed")

    assert "requestState" not in waiting
    assert updated["result"]["resultType"] == "complete"
    assert completed["result"]["resultType"] == "complete"


def test_tasks_cancel_is_empty_cooperative_acknowledgement() -> None:
    with TestClient(app=_make_task_app()) as client:
        created = _rpc(
            client,
            "tools/call",
            {"name": "optional_task", "arguments": {"delay": 1}},
            tasks_capable=True,
        )["result"]
        cancelled = _rpc(client, "tasks/cancel", {"taskId": created["taskId"]}, tasks_capable=True)
        terminal = _wait_for_status(client, created["taskId"], "cancelled")

    assert cancelled["result"]["resultType"] == "complete"
    assert terminal["status"] == "cancelled"


def test_task_record_is_retrievable_from_injected_store() -> None:
    store = MemoryStore()
    task_config = MCPTaskConfig(store=store)
    app = _make_task_app(task_config)
    with TestClient(app=app) as client:
        created = _rpc(
            client,
            "tools/call",
            {"name": "optional_task", "arguments": {"delay": 0}},
            tasks_capable=True,
        )["result"]
        _wait_for_status(client, created["taskId"], "completed")

    with TestClient(app=_make_task_app(task_config)) as client:
        recovered = _rpc(client, "tasks/get", {"taskId": created["taskId"]}, tasks_capable=True)["result"]

    assert recovered["status"] == "completed"


def test_removed_legacy_task_methods_are_not_registered() -> None:
    with TestClient(app=_make_task_app()) as client:
        for method in ("tasks/list", "tasks/result"):
            response = _rpc(client, method, tasks_capable=True)
            assert response["error"]["code"] == -32601
