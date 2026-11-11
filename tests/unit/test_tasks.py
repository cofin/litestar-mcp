"""MCP 2026-07-28 Tasks extension tests."""

import asyncio
import contextlib
import json
import threading
import time
from typing import Any, cast

import msgspec
import pytest
from litestar import Litestar, get
from litestar.stores.memory import MemoryStore
from litestar.testing import AsyncTestClient, TestClient

from litestar_mcp import (
    LitestarMCP,
    MCPConfig,
    MCPInputRequiredResult,
    MCPTaskConfig,
    get_mcp_request_context,
)
from litestar_mcp.core.serialization import from_json
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.service import TASKS_EXTENSION
from litestar_mcp.mcp.stdio import ASGIStreamingTransport
from litestar_mcp.mcp.tasks import MCPTaskStore, ToolTaskCall
from litestar_mcp.utils import mcp_tool
from tests.unit.conftest import mcp_envelope, mcp_post


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    tasks_capable: bool = False,
) -> dict[str, Any]:
    request_params = dict(params or {})
    if tasks_capable:
        request_params["_meta"] = {"io.modelcontextprotocol/clientCapabilities": {"extensions": {TASKS_EXTENSION: {}}}}
    return cast("dict[str, Any]", mcp_post(client, method, request_params).json())


def _make_task_app(task_config: MCPTaskConfig | None = None) -> Litestar:
    @get("/optional-task")
    @mcp_tool(name="optional_task", task_support="optional")
    async def optional_task(delay: float = 0.01) -> dict[str, str]:
        import asyncio

        await asyncio.sleep(delay)
        return {"status": "completed"}

    @get("/required-task")
    @mcp_tool(name="required_task", task_support="required")
    async def required_task(delay: float = 0.01) -> dict[str, str]:
        import asyncio

        await asyncio.sleep(delay)
        return {"status": "completed"}

    @get("/forbidden-task")
    @mcp_tool(name="forbidden_task", task_support="forbidden")
    async def forbidden_task() -> dict[str, str]:
        return {"status": "sync"}

    @get("/input-task")
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


def test_task_promoted_tool_ignores_request_progress_stream() -> None:
    @get("/progress-task")
    @mcp_tool(name="progress_task", task_support="optional")
    async def progress_task() -> dict[str, str]:
        context = get_mcp_request_context()
        await context.report_progress(1, total=2)
        return {"status": "completed"}

    app = Litestar(route_handlers=[progress_task], plugins=[LitestarMCP(MCPConfig(tasks=True))])
    with TestClient(app=app) as client:
        response = mcp_post(
            client,
            "tools/call",
            {
                "name": "progress_task",
                "arguments": {},
                "_meta": {
                    "progressToken": "tok",
                    "io.modelcontextprotocol/clientCapabilities": {"extensions": {TASKS_EXTENSION: {}}},
                },
            },
        )
        assert response.status_code == 200
        frames = [line for line in response.text.splitlines() if line.startswith("data:")]
        task = json.loads(frames[-1].removeprefix("data:"))["result"]
        assert task["resultType"] == "task"
        result = _wait_for_status(client, task["taskId"], "completed")

    assert result["status"] == "completed"
    assert result["result"]["isError"] is False


def test_task_store_defaults_to_app_store_registry() -> None:
    shared_store = MemoryStore()
    plugin = LitestarMCP(MCPConfig(tasks=True))
    app = Litestar(plugins=[plugin], stores={"litestar_mcp_tasks": shared_store})

    assert plugin.task_store is not None
    assert plugin.task_store.store is shared_store
    assert app.stores.get("litestar_mcp_tasks") is shared_store


async def _wait_threading_event(flag: threading.Event) -> None:
    for _ in range(400):
        if flag.is_set():
            return
        await asyncio.sleep(0.005)
    raise asyncio.TimeoutError


async def _fetch_task_record(client: AsyncTestClient[Any], task_id: str) -> dict[str, Any]:
    get_body, get_headers = mcp_envelope(
        "tasks/get",
        {
            "taskId": task_id,
            "_meta": {
                "io.modelcontextprotocol/clientCapabilities": {"extensions": {TASKS_EXTENSION: {}}},
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            },
        },
    )
    get_resp = await client.post("/mcp", json=get_body, headers=get_headers)
    assert get_resp.status_code == 200
    return cast("dict[str, Any]", get_resp.json()["result"])


@pytest.mark.anyio
async def test_inprocess_promoted_progress_updates_status_message() -> None:
    """In-process promoted task updates statusMessage via report_progress and clears it upon completion."""
    phase1_gate = threading.Event()
    phase1_done = threading.Event()
    phase2_gate = threading.Event()
    phase2_done = threading.Event()
    finish_gate = threading.Event()

    @get("/progress-status")
    @mcp_tool(name="progress_status_task", task_support="optional")
    async def progress_status_task() -> dict[str, bool]:
        context = get_mcp_request_context()
        await _wait_threading_event(phase1_gate)
        await context.report_progress(1, total=2, message="Phase 1")
        phase1_done.set()
        await _wait_threading_event(phase2_gate)
        await context.report_progress(2, total=2)
        phase2_done.set()
        await _wait_threading_event(finish_gate)
        return {"ok": True}

    app = Litestar(
        route_handlers=[progress_status_task],
        plugins=[LitestarMCP(MCPConfig(tasks=True))],
    )
    caps_meta = {
        "progressToken": "status-tok",
        "io.modelcontextprotocol/clientCapabilities": {"extensions": {TASKS_EXTENSION: {}}},
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    }

    async with AsyncTestClient(app=app) as client:
        call_body, call_headers = mcp_envelope(
            "tools/call",
            {"name": "progress_status_task", "arguments": {}, "_meta": caps_meta},
        )
        call_resp = await client.post("/mcp", json=call_body, headers=call_headers)
        assert call_resp.status_code == 200
        frames = [line for line in call_resp.text.splitlines() if line.startswith("data:")]
        task_result = cast("dict[str, Any]", from_json(frames[-1].removeprefix("data:")))["result"]
        assert task_result["resultType"] == "task"
        task_id = task_result["taskId"]

        phase1_gate.set()
        await _wait_threading_event(phase1_done)
        working_phase1 = await _fetch_task_record(client, task_id)
        assert working_phase1["status"] == "working"
        assert working_phase1["statusMessage"] == "Phase 1"

        phase2_gate.set()
        await _wait_threading_event(phase2_done)
        working_phase2 = await _fetch_task_record(client, task_id)
        assert working_phase2["status"] == "working"
        assert working_phase2["statusMessage"] == "Phase 1"

        finish_gate.set()
        completed_record: dict[str, Any] = {}
        for _ in range(100):
            completed_record = await _fetch_task_record(client, task_id)
            if completed_record["status"] == "completed":
                break
            await asyncio.sleep(0.01)

        assert completed_record["status"] == "completed"
        assert "statusMessage" not in completed_record


@pytest.mark.parametrize("progress_token", ["str-tok", 99, None])
def test_tool_task_call_serialization_roundtrip(progress_token: str | int | None) -> None:
    """ToolTaskCall preserves progress_token, session_id, and log_level across msgspec serialization."""
    call = ToolTaskCall(
        task_id="task-1",
        tool_name="sample_tool",
        arguments={"x": 1},
        owner_id="user-1",
        client_id="client-1",
        protocol_version="2026-07-28",
        progress_token=progress_token,
        session_id="sess-rt",
        log_level="warning",
    )
    encoded = msgspec.to_builtins(call)
    decoded = msgspec.convert(encoded, ToolTaskCall)
    assert decoded.progress_token == progress_token
    assert decoded.session_id == "sess-rt"
    assert decoded.log_level == "warning"


def test_tool_task_call_legacy_payload_defaults_new_fields_to_none() -> None:
    """Legacy serialized ToolTaskCall payloads missing newer optional fields decode with None defaults."""
    legacy_payload = {
        "task_id": "legacy-task",
        "tool_name": "sample_tool",
        "arguments": {},
    }
    decoded = msgspec.convert(legacy_payload, ToolTaskCall)
    assert decoded.progress_token is None
    assert decoded.session_id is None
    assert decoded.log_level is None


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", ["completed", "cancelled", "missing-id"])
async def test_update_status_message_terminal_and_missing_guards(scenario: str) -> None:
    """MCPTaskStore.update_status_message rejects missing tasks and leaves terminal task statusMessage untouched."""
    store = MCPTaskStore(MemoryStore())

    if scenario == "missing-id":
        assert await store.update_status_message("missing-id", "late update") is None
        return

    record = await store.create("owner-1")
    if scenario == "completed":
        await store.complete(record.task_id, result={"content": []})
    else:
        await store.cancel(record.task_id, "owner-1")
        await store.mark_cancelled(record.task_id)

    expected_message = "The task was cancelled." if scenario == "cancelled" else None
    updated = await store.update_status_message(record.task_id, "late update")
    assert updated is not None
    assert updated.status == scenario
    assert updated.status_message == expected_message
    persisted = await store.get(record.task_id, "owner-1")
    assert persisted is not None
    assert persisted.status_message == expected_message


@pytest.mark.anyio
async def test_inprocess_multi_round_resume_refreshes_log_level() -> None:
    """Resuming an in-process task via tasks/update picks up updated session log levels."""

    @get("/resume-log-task")
    @mcp_tool(name="resume_log_task", task_support="optional")
    async def resume_log_task() -> MCPInputRequiredResult | dict[str, str]:
        context = get_mcp_request_context()
        if not context.input_responses:
            return MCPInputRequiredResult(
                input_requests={"confirm": {"type": "boolean"}},
                request_state="round-1",
            )
        await context.log("debug", "drop-on-resume")
        await context.log("error", "keep-on-resume")
        return {"resumed": "ok"}

    plugin = LitestarMCP(MCPConfig(tasks=True))
    app = Litestar(route_handlers=[resume_log_task], plugins=[plugin])

    async with (
        app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver.local/mcp",
            transport=ASGIStreamingTransport(app),
            protocol_version="2025-11-25",
            capabilities={"extensions": {TASKS_EXTENSION: {}}},
        ) as session_client,
    ):
        await session_client.initialize()
        assert session_client.session_id is not None
        session_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _read_sse() -> None:
            async for payload in session_client.open_event_stream():
                await session_events.put(payload)

        sse_task = asyncio.create_task(_read_sse())
        try:
            for _ in range(400):
                if plugin.registry.has_subscription_manager and plugin.registry.subscription_manager._streams:
                    break
                await asyncio.sleep(0.005)
            else:
                raise asyncio.TimeoutError

            task_res = await session_client.request("tools/call", {"name": "resume_log_task", "arguments": {}})
            task_id = task_res["taskId"]

            for _ in range(100):
                current = await session_client.request("tasks/get", {"taskId": task_id})
                if current["status"] == "input_required":
                    break
                await asyncio.sleep(0.01)

            await session_client.set_logging_level("warning")
            await session_client.request(
                "tasks/update",
                {"taskId": task_id, "inputResponses": {"confirm": True}},
            )

            for _ in range(100):
                current = await session_client.request("tasks/get", {"taskId": task_id})
                if current["status"] == "completed":
                    break
                await asyncio.sleep(0.01)

            received_logs: list[dict[str, Any]] = []
            while True:
                event = await asyncio.wait_for(session_events.get(), timeout=2.0)
                if event.get("method") == "notifications/message":
                    received_logs.append(event["params"])
                    if event["params"].get("data") == "keep-on-resume":
                        break

            assert all(entry.get("data") != "drop-on-resume" for entry in received_logs)
            assert any(
                entry.get("level") == "error" and entry.get("data") == "keep-on-resume" for entry in received_logs
            )
        finally:
            sse_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sse_task
