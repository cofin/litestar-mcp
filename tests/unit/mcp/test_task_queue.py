"""Unit tests for distributed MCP Tasks on litestar-queues via QueueTaskExecutor."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID, uuid4

import anyio
import httpx2
import pytest
from litestar import Controller, Litestar, Request, get
from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend
from litestar.connection import ASGIConnection
from litestar.di import Provide
from litestar.exceptions import ImproperlyConfiguredException
from litestar.status_codes import HTTP_200_OK
from litestar.stores.memory import MemoryStore
from litestar.testing import AsyncTestClient

pytest.importorskip("litestar_queues")

from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.litestar import FromDishka, inject, setup_dishka
from litestar_queues import QueueConfig, QueuePlugin, QueueService
from litestar_queues.config import WorkerConfig
from litestar_queues.worker import Worker

from litestar_mcp import (
    LitestarMCP,
    MCPConfig,
    MCPInputRequiredResult,
    MCPTaskConfig,
    get_mcp_request_context,
)
from litestar_mcp.core.jsonrpc import INTERNAL_ERROR, JSONRPCError, JSONRPCErrorException
from litestar_mcp.core.serialization import from_json
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.plugin import TASK_STORE_NAME
from litestar_mcp.mcp.service import SESSION_STORE_NAME, TASKS_EXTENSION
from litestar_mcp.mcp.stdio import ASGIStreamingTransport
from litestar_mcp.mcp.tasks import (
    ToolTaskCall,
    _FallbackAuthorizationSnapshot,
    _FallbackCredentialRestrictions,
    _FallbackPrincipal,
    _FallbackSecurityContext,
)
from litestar_mcp.queues import QueueTaskExecutor
from litestar_mcp.utils import mcp_tool
from tests.unit.conftest import mcp_envelope


def _auth_guard(connection: ASGIConnection[Any, Any, Any, Any], _: Any) -> None:
    """Guard requiring connection.user to be set."""
    if not getattr(connection, "user", None):
        msg = "Authentication required"
        raise PermissionError(msg)


async def _load_user(owner_id: str | None) -> dict[str, str] | None:
    """Sample principal loader rehydrating scope['user'] from owner_id."""
    if owner_id in ("alice", "user:alice"):
        return {"id": "alice", "name": "Alice"}
    return None


def _make_queued_app(
    *,
    executor: QueueTaskExecutor | None,
    execution_backend: str = "immediate",
    include_queue_plugin: bool = True,
    status_callback: Any = None,
    extra_handlers: list[Any] | None = None,
) -> tuple[Litestar, QueueTaskExecutor | None]:
    @get("/slow-echo")
    @mcp_tool(name="slow_echo", task_support="optional")
    async def slow_echo(text: str = "hello") -> dict[str, str]:
        await asyncio.sleep(0.01)
        return {"echo": text}

    @get("/whoami", guards=[_auth_guard])
    @mcp_tool(name="whoami", task_support="optional")
    async def whoami(request: Request[Any, Any, Any]) -> dict[str, str]:
        return {"user_id": str(request.user["id"])}

    @get("/ask")
    @mcp_tool(name="ask", task_support="optional")
    async def ask() -> MCPInputRequiredResult:
        return MCPInputRequiredResult(
            input_requests={"confirm": {"type": "boolean", "description": "Confirm execution"}},
        )

    @get("/protocol-fail")
    @mcp_tool(name="protocol_fail", task_support="optional")
    async def protocol_fail() -> None:
        raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message="protocol failure"))

    plugins: list[Any] = []
    if include_queue_plugin and executor is not None:
        queue_config = QueueConfig(
            queue_backend="memory",
            execution_backend=execution_backend,
            worker=WorkerConfig(placement="external"),
        )
        plugins.append(QueuePlugin(queue_config))

    mcp_task_config = MCPTaskConfig(
        executor=executor,
        status_callback=status_callback,
    )
    mcp_plugin = LitestarMCP(MCPConfig(tasks=mcp_task_config))
    plugins.append(mcp_plugin)

    def user_header_middleware(app: Any) -> Any:
        async def asgi_handler(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                for name, value in scope.get("headers", []):
                    if name.lower() == b"x-user-id":
                        user_id = value.decode("utf-8")
                        scope["user"] = {"id": user_id, "name": user_id.capitalize()}
                        break
            await app(scope, receive, send)

        return asgi_handler

    app = Litestar(
        route_handlers=[slow_echo, whoami, ask, protocol_fail, *(extra_handlers or [])],
        plugins=plugins,
        middleware=[user_header_middleware],
    )
    return app, executor


async def _mcp_post(
    client: AsyncTestClient[Litestar],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    tasks_capable: bool = True,
    expected_status: int | None = None,
) -> dict[str, Any]:
    request_params = dict(params or {})
    if tasks_capable:
        meta = dict(request_params.get("_meta") or {})
        caps = dict(meta.get("io.modelcontextprotocol/clientCapabilities") or {})
        extensions = dict(caps.get("extensions") or {})
        extensions[TASKS_EXTENSION] = {}
        caps["extensions"] = extensions
        meta["io.modelcontextprotocol/clientCapabilities"] = caps
        meta["io.modelcontextprotocol/protocolVersion"] = "2026-07-28"
        request_params["_meta"] = meta

    body, req_headers = mcp_envelope(method, request_params, headers=headers)
    response = await client.post(
        "/mcp",
        json=body,
        headers=req_headers,
    )
    if expected_status is not None:
        assert response.status_code == expected_status
    else:
        assert response.status_code in (HTTP_200_OK, 400)
    return cast("dict[str, Any]", response.json())


@pytest.mark.anyio
async def test_unbound_executor_raises() -> None:
    """QueueTaskExecutor.runtime raises RuntimeError before being bound to an app."""
    executor = QueueTaskExecutor()
    with pytest.raises(RuntimeError, match="QueueTaskExecutor is not bound"):
        _ = executor.runtime


def test_missing_queue_plugin_raises() -> None:
    """Configuring QueueTaskExecutor without QueuePlugin fails while building the app."""
    with pytest.raises(ImproperlyConfiguredException, match="requires QueuePlugin"):
        _make_queued_app(executor=QueueTaskExecutor(), include_queue_plugin=False)


def test_executor_attaches_resolver_to_queue_plugin() -> None:
    """LitestarMCP attaches the executor's resolver to the app's QueuePlugin during app init."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor)
    queue_plugin = app.plugins.get(QueuePlugin)
    assert queue_plugin.config.task_dependency_resolver == executor.resolve_task_dependencies


@pytest.mark.anyio
async def test_queue_executor_completes_task_immediate() -> None:
    """Immediate backend enqueues, executes inline, records job_id, and completes task."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "slow_echo", "arguments": {"text": "distributed"}},
        )
        assert result["result"]["resultType"] == "task"
        task_id = result["result"]["taskId"]

        store = executor.runtime.task_store
        job_id = await store.get_job_id(task_id)
        assert job_id is not None
        assert UUID(job_id)

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        task_record = status_result["result"]
        assert task_record["status"] == "completed"
        import json

        assert json.loads(task_record["result"]["content"][0]["text"]) == {"echo": "distributed"}


@pytest.mark.anyio
async def test_queue_executor_worker_path_local() -> None:
    """Local backend leaves task in working state until Worker runs attempt."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="local")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "slow_echo", "arguments": {"text": "worker-path"}},
        )
        task_id = result["result"]["taskId"]

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "working"

        queue_plugin = app.plugins.get(QueuePlugin)
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)
        await worker.run_once()
        await worker.stop()

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"
        import json

        assert json.loads(status_result["result"]["result"]["content"][0]["text"]) == {"echo": "worker-path"}


@pytest.mark.anyio
async def test_worker_runs_with_loaded_principal() -> None:
    """Worker loads user via principal_loader; fails closed without loader."""
    executor_with_loader = QueueTaskExecutor(principal_loader=_load_user)
    app_auth, _ = _make_queued_app(executor=executor_with_loader, execution_backend="immediate")
    async with AsyncTestClient(app_auth) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "whoami", "arguments": {}},
            headers={"X-User-Id": "alice"},
        )
        task_id = result["result"]["taskId"]
        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"
        import json

        assert json.loads(status_result["result"]["result"]["content"][0]["text"]) == {"user_id": "alice"}

    executor_no_loader = QueueTaskExecutor(principal_loader=None)
    app_no_auth, _ = _make_queued_app(executor=executor_no_loader, execution_backend="immediate")
    async with AsyncTestClient(app_no_auth) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "whoami", "arguments": {}},
        )
        task_id = result["result"]["taskId"]
        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"
        assert status_result["result"]["result"]["isError"] is True
        import json

        error_payload = json.loads(status_result["result"]["result"]["content"][0]["text"])
        assert "Authentication required" in error_payload["error"]


@pytest.mark.anyio
async def test_worker_fails_on_protocol_error() -> None:
    """Worker transitions task to failed on JSONRPCErrorException."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="local")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "protocol_fail", "arguments": {}},
        )
        task_id = result["result"]["taskId"]
        queue_plugin = app.plugins.get(QueuePlugin)
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)
        await worker.run_once()
        await worker.stop()

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "failed"
        assert "protocol failure" in status_result["result"]["error"]["message"]


@pytest.mark.anyio
async def test_input_required_ends_job() -> None:
    """Tool returning input_required transitions task to input_required and ends the job."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "ask", "arguments": {}},
        )
        task_id = result["result"]["taskId"]
        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        record = status_result["result"]
        assert record["status"] == "input_required"
        assert "confirm" in record["inputRequests"]


@pytest.mark.anyio
async def test_worker_transitions_publish_notifications() -> None:
    """Worker transitions trigger status_callback for notifications fan-out."""
    observed_statuses: list[str] = []

    async def _on_status(record: Any) -> None:
        observed_statuses.append(record.status)

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(
        executor=executor,
        execution_backend="immediate",
        status_callback=_on_status,
    )
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "slow_echo", "arguments": {"text": "notifications"}},
        )
        task_id = result["result"]["taskId"]
        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"

    assert "working" in observed_statuses
    assert "completed" in observed_statuses


@pytest.mark.anyio
async def test_dispatch_not_on_wire() -> None:
    """Internal fields (dispatch, jobId, dispatchInternal, inputResponsesInternal) never leak on wire."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "slow_echo", "arguments": {"text": "clean-wire"}},
        )
        task_id = result["result"]["taskId"]
        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        record = status_result["result"]

        for forbidden_key in ("dispatch", "jobId", "dispatchInternal", "inputResponsesInternal"):
            assert forbidden_key not in record


@pytest.mark.anyio
async def test_cancel_pending_job() -> None:
    """Cancelling a pending job marks record cancelled and prevents worker from executing it."""
    call_count = 0

    @get("/countable")
    @mcp_tool(name="countable", task_support="optional")
    async def countable() -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        return {"counted": "yes"}

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="local", extra_handlers=[countable])
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "countable", "arguments": {}},
        )
        task_id = result["result"]["taskId"]

        cancel_result = await _mcp_post(client, "tasks/cancel", {"taskId": task_id})
        assert "error" not in cancel_result

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "cancelled"

        queue_plugin = app.plugins.get(QueuePlugin)
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)
        await worker.run_once()
        await worker.stop()

        assert call_count == 0


@pytest.mark.anyio
async def test_cancel_reaches_running_worker() -> None:
    """Durable cancellation stops running worker attempt cooperatively."""
    started_event = asyncio.Event()
    block_event = asyncio.Event()

    @get("/blocking-tool")
    @mcp_tool(name="blocking_tool", task_support="optional")
    async def blocking_tool() -> dict[str, str]:
        started_event.set()
        await block_event.wait()
        return {"result": "done"}

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="local", extra_handlers=[blocking_tool])
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "blocking_tool", "arguments": {}},
        )
        task_id = result["result"]["taskId"]

        queue_plugin = app.plugins.get(QueuePlugin)
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)

        with anyio.fail_after(2):
            async with anyio.create_task_group() as tg:
                tg.start_soon(worker.run_once)
                await started_event.wait()

                cancel_result = await _mcp_post(client, "tasks/cancel", {"taskId": task_id})
                assert "error" not in cancel_result

        await worker.stop()

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "cancelled"


@pytest.mark.anyio
async def test_cancel_input_required_task() -> None:
    """Cancelling a task in input_required state marks it cancelled without active queue job."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "ask", "arguments": {}},
        )
        task_id = result["result"]["taskId"]

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "input_required"

        cancel_result = await _mcp_post(client, "tasks/cancel", {"taskId": task_id})
        assert "error" not in cancel_result

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "cancelled"


@pytest.mark.anyio
async def test_cancel_terminal_is_noop() -> None:
    """Cancelling an already completed task leaves status as completed."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "slow_echo", "arguments": {"text": "terminal-test"}},
        )
        task_id = result["result"]["taskId"]

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"

        cancel_result = await _mcp_post(client, "tasks/cancel", {"taskId": task_id})
        assert "error" not in cancel_result

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"


@pytest.mark.anyio
async def test_cancel_other_owner_rejected() -> None:
    """Cancelling a task with a mismatched owner returns INVALID_PARAMS error."""
    executor = QueueTaskExecutor(principal_loader=_load_user)
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate")
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "whoami", "arguments": {}},
            headers={"X-User-Id": "alice"},
        )
        task_id = result["result"]["taskId"]

        cancel_result = await _mcp_post(
            client,
            "tasks/cancel",
            {"taskId": task_id},
            headers={"X-User-Id": "bob"},
        )
        assert "error" in cancel_result
        assert cancel_result["error"]["code"] == -32602


@pytest.mark.anyio
async def test_input_required_resumes_on_update() -> None:
    """Task in input_required resumes with input_responses and request_state when updated."""

    @get("/interactive-calc")
    @mcp_tool(name="interactive_calc", task_support="optional")
    async def interactive_calc() -> MCPInputRequiredResult | dict[str, Any]:
        context = get_mcp_request_context()
        if context.input_responses is None:
            return MCPInputRequiredResult(
                input_requests={"q": {"type": "string", "description": "What is the question?"}},
                request_state="s1",
            )
        assert context.request_state == "s1"
        return {"answer": context.input_responses["q"]}

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate", extra_handlers=[interactive_calc])
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "interactive_calc", "arguments": {}},
        )
        task_id = result["result"]["taskId"]

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "input_required"

        update_result = await _mcp_post(
            client,
            "tasks/update",
            {"taskId": task_id, "inputResponses": {"q": "42"}},
        )
        assert "error" not in update_result

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"
        import json

        content = json.loads(status_result["result"]["result"]["content"][0]["text"])
        assert content == {"answer": "42"}


@pytest.mark.anyio
async def test_partial_update_waits_for_all_responses() -> None:
    """Partial update persists responses and keeps task at input_required until complete."""

    @get("/multi-input")
    @mcp_tool(name="multi_input", task_support="optional")
    async def multi_input() -> MCPInputRequiredResult | dict[str, Any]:
        context = get_mcp_request_context()
        if context.input_responses is None:
            return MCPInputRequiredResult(
                input_requests={
                    "first": {"type": "string", "description": "First input"},
                    "second": {"type": "string", "description": "Second input"},
                },
            )
        return {
            "first": context.input_responses["first"],
            "second": context.input_responses["second"],
        }

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate", extra_handlers=[multi_input])
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "multi_input", "arguments": {}},
        )
        task_id = result["result"]["taskId"]

        update1 = await _mcp_post(
            client,
            "tasks/update",
            {"taskId": task_id, "inputResponses": {"first": "alpha"}},
        )
        assert "error" not in update1

        status1 = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status1["result"]["status"] == "input_required"
        assert "second" in status1["result"]["inputRequests"]
        assert "first" not in status1["result"]["inputRequests"]

        update2 = await _mcp_post(
            client,
            "tasks/update",
            {"taskId": task_id, "inputResponses": {"second": "beta"}},
        )
        assert "error" not in update2

        status2 = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status2["result"]["status"] == "completed"
        import json

        content = json.loads(status2["result"]["result"]["content"][0]["text"])
        assert content == {"first": "alpha", "second": "beta"}


@pytest.mark.anyio
async def test_resume_on_other_replica() -> None:
    """Local worker processes initial input_required attempt, resumes on second worker run."""

    @get("/local-interactive")
    @mcp_tool(name="local_interactive", task_support="optional")
    async def local_interactive() -> MCPInputRequiredResult | dict[str, Any]:
        context = get_mcp_request_context()
        if context.input_responses is None:
            return MCPInputRequiredResult(
                input_requests={"confirm": {"type": "boolean", "description": "Proceed?"}},
            )
        return {"proceeded": context.input_responses["confirm"]}

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="local", extra_handlers=[local_interactive])
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "local_interactive", "arguments": {}},
        )
        task_id = result["result"]["taskId"]

        queue_plugin = app.plugins.get(QueuePlugin)
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)

        await worker.run_once()
        await worker.stop()

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "input_required"

        update_result = await _mcp_post(
            client,
            "tasks/update",
            {"taskId": task_id, "inputResponses": {"confirm": True}},
        )
        assert "error" not in update_result

        status_before_worker = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_before_worker["result"]["status"] == "working"

        worker2 = Worker(service, queue_plugin.config.worker)
        await worker2.run_once()
        await worker2.stop()

        status_after_worker = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_after_worker["result"]["status"] == "completed"
        import json

        content = json.loads(status_after_worker["result"]["result"]["content"][0]["text"])
        assert content == {"proceeded": True}


@pytest.mark.anyio
async def test_resume_attempt_numbers_increase() -> None:
    """Subsequent input rounds increment the attempt counter on persisted dispatch."""
    round_count = 0

    @get("/two-round-input")
    @mcp_tool(name="two_round_input", task_support="optional")
    async def two_round_input() -> MCPInputRequiredResult | dict[str, Any]:
        nonlocal round_count
        context = get_mcp_request_context()
        if context.input_responses is None:
            round_count = 1
            return MCPInputRequiredResult(
                input_requests={"step1": {"type": "string", "description": "Step 1"}},
            )
        if "step1" in context.input_responses and "step2" not in context.input_responses:
            round_count = 2
            return MCPInputRequiredResult(
                input_requests={"step2": {"type": "string", "description": "Step 2"}},
            )
        round_count = 3
        return {"step1": context.input_responses["step1"], "step2": context.input_responses["step2"]}

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="immediate", extra_handlers=[two_round_input])
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "two_round_input", "arguments": {}},
        )
        task_id = result["result"]["taskId"]

        store = executor.runtime.task_store
        dispatch1 = await store.get_dispatch(task_id)
        assert dispatch1 is not None
        assert dispatch1.attempt == 1

        update1 = await _mcp_post(
            client,
            "tasks/update",
            {"taskId": task_id, "inputResponses": {"step1": "val1"}},
        )
        assert "error" not in update1

        dispatch2 = await store.get_dispatch(task_id)
        assert dispatch2 is not None
        assert dispatch2.attempt == 2

        update2 = await _mcp_post(
            client,
            "tasks/update",
            {"taskId": task_id, "inputResponses": {"step2": "val2"}},
        )
        assert "error" not in update2

        dispatch3 = await store.get_dispatch(task_id)
        assert dispatch3 is not None
        assert dispatch3.attempt == 3

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"


@pytest.mark.anyio
async def test_standalone_worker_lifespan_runs_tool_attempt() -> None:
    """A standalone worker enters the app lifespan, binds the executor, and completes a tool attempt."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="local")
    queue_plugin = app.plugins.get(QueuePlugin)

    async with queue_plugin.open_worker_service(app) as service:
        await service.open()
        try:
            store = executor.runtime.task_store
            record = await store.create(None, local=False)
            call = ToolTaskCall(task_id=record.task_id, tool_name="slow_echo", arguments={"text": "standalone"})
            await store.set_dispatch(record.task_id, call)
            await executor.submit(record, call)
            worker = Worker(service, queue_plugin.config.worker)
            await worker.run_once()
            await worker.stop()
            finished = await store.get(record.task_id, None)
        finally:
            await service.close()

    assert finished.status == "completed"
    assert finished.result is not None
    assert json.loads(finished.result["content"][0]["text"]) == {"echo": "standalone"}


@dataclass(frozen=True, slots=True)
class _DomainUser:
    """Domain user value object for Provide dependency rehydration tests."""

    id: str
    role: str


class _RequestTraceService:
    """Request-scoped Dishka service tracking container lifecycle."""

    def __init__(self) -> None:
        self.token = uuid4().hex
        self.closed = False


class _RequestTraceProvider(Provider):
    """Dishka provider yielding a request-scoped _RequestTraceService."""

    scope = Scope.REQUEST

    def __init__(self, lifecycle_events: list[str]) -> None:
        super().__init__()
        self._lifecycle_events = lifecycle_events

    @provide
    async def provide_service(self) -> AsyncIterator[_RequestTraceService]:
        service = _RequestTraceService()
        self._lifecycle_events.append(f"open:{service.token}")
        try:
            yield service
        finally:
            service.closed = True
            self._lifecycle_events.append(f"close:{service.token}")


@pytest.mark.anyio
async def test_zero_config_auto_wires_queue_task_executor() -> None:
    """Registering QueuePlugin and LitestarMCP() auto-enables tasks and auto-wires QueueTaskExecutor."""

    @get("/auto-echo")
    @mcp_tool(name="auto_echo", task=True)
    async def auto_echo(text: str = "zero-config") -> dict[str, str]:
        return {"echo": text}

    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="immediate",
            worker=WorkerConfig(placement="external"),
        )
    )
    mcp_plugin = LitestarMCP()
    app = Litestar(route_handlers=[auto_echo], plugins=[queue_plugin, mcp_plugin])

    assert mcp_plugin.config.task_config is not None
    assert isinstance(mcp_plugin.config.task_config.executor, QueueTaskExecutor)

    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "auto_echo", "arguments": {"text": "auto-wired"}},
        )
        assert result["result"]["resultType"] == "task"
        task_id = result["result"]["taskId"]

        status_result = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        assert status_result["result"]["status"] == "completed"
        assert json.loads(status_result["result"]["result"]["content"][0]["text"]) == {"echo": "auto-wired"}


@pytest.mark.anyio
async def test_zero_config_auto_rehydrates_authenticated_user_and_fails_closed_when_anonymous() -> None:
    """Zero-config QueueTaskExecutor snapshots and rehydrates request.user (key and attribute access) and fails closed when anonymous."""

    @get("/profile-inspect", guards=[_auth_guard])
    @mcp_tool(name="profile_inspect", task=True)
    async def profile_inspect(request: Request[Any, Any, Any]) -> dict[str, str]:
        return {
            "key_id": str(request.user["id"]),
            "attr_id": str(request.user.id),
            "attr_name": str(request.user.name),
            "nested_tier_attr": str(request.user.profile.tier),
            "nested_tier_key": str(request.user["profile"]["tier"]),
        }

    def rich_user_middleware(app: Any) -> Any:
        async def asgi_handler(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                for name, value in scope.get("headers", []):
                    if name.lower() == b"x-user-id":
                        uid = value.decode("utf-8")
                        scope["user"] = {
                            "id": uid,
                            "name": uid.capitalize(),
                            "profile": {"tier": "enterprise"},
                        }
                        break
            await app(scope, receive, send)

        return asgi_handler

    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="immediate",
            worker=WorkerConfig(placement="external"),
        )
    )
    app = Litestar(
        route_handlers=[profile_inspect],
        plugins=[queue_plugin, LitestarMCP()],
        middleware=[rich_user_middleware],
    )

    async with AsyncTestClient(app) as client:
        auth_result = await _mcp_post(
            client,
            "tools/call",
            {"name": "profile_inspect", "arguments": {}},
            headers={"X-User-Id": "alice"},
        )
        auth_task_id = auth_result["result"]["taskId"]
        auth_status = await _mcp_post(
            client,
            "tasks/get",
            {"taskId": auth_task_id},
            headers={"X-User-Id": "alice"},
        )
        assert auth_status["result"]["status"] == "completed"
        assert json.loads(auth_status["result"]["result"]["content"][0]["text"]) == {
            "key_id": "alice",
            "attr_id": "alice",
            "attr_name": "Alice",
            "nested_tier_attr": "enterprise",
            "nested_tier_key": "enterprise",
        }

        anon_result = await _mcp_post(
            client,
            "tools/call",
            {"name": "profile_inspect", "arguments": {}},
        )
        anon_task_id = anon_result["result"]["taskId"]
        anon_status = await _mcp_post(client, "tasks/get", {"taskId": anon_task_id})
        assert anon_status["result"]["status"] == "completed"
        assert anon_status["result"]["result"]["isError"] is True


@pytest.mark.anyio
async def test_worker_rehydrates_from_existing_litestar_provide_dependency() -> None:
    """Worker resolves existing current_user Provide dependency for guards, handler params, and request.user."""

    def require_admin_role(connection: ASGIConnection[Any, Any, Any, Any], _: Any) -> None:
        user = getattr(connection, "user", None)
        if getattr(user, "role", None) != "admin":
            msg = "Admin role required"
            raise PermissionError(msg)

    async def provide_current_user(request: Request[Any, Any, Any]) -> _DomainUser:
        return _DomainUser(id=str(request.user["id"]), role="admin")

    @get(
        "/admin-report",
        guards=[require_admin_role],
        dependencies={"current_user": Provide(provide_current_user)},
    )
    @mcp_tool(name="admin_report", task=True)
    async def admin_report(
        current_user: _DomainUser,
        request: Request[Any, Any, Any],
    ) -> dict[str, str]:
        assert isinstance(current_user, _DomainUser)
        assert isinstance(request.user, _DomainUser)
        return {"id": current_user.id, "role": request.user.role}

    app, _ = _make_queued_app(
        executor=QueueTaskExecutor(),
        execution_backend="immediate",
        extra_handlers=[admin_report],
    )
    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "admin_report", "arguments": {}},
            headers={"X-User-Id": "alice"},
        )
        task_id = result["result"]["taskId"]
        status_result = await _mcp_post(
            client,
            "tasks/get",
            {"taskId": task_id},
            headers={"X-User-Id": "alice"},
        )
        assert status_result["result"]["status"] == "completed"
        assert json.loads(status_result["result"]["result"]["content"][0]["text"]) == {
            "id": "alice",
            "role": "admin",
        }


@pytest.mark.anyio
async def test_dishka_request_scoped_dependency_in_queued_and_in_process_tasks() -> None:
    """Both QueueTaskExecutor attempts and detached in-process tasks enter fresh Scope.REQUEST Dishka containers."""
    lifecycle_events: list[str] = []

    @get("/dishka-queued")
    @mcp_tool(name="dishka_queued", task=True)
    @inject
    async def dishka_queued(svc: FromDishka[_RequestTraceService]) -> dict[str, Any]:
        return {"token": svc.token, "closed": svc.closed}

    @get("/dishka-inproc")
    @mcp_tool(name="dishka_inproc", task=True, queue=False)
    @inject
    async def dishka_inproc(svc: FromDishka[_RequestTraceService]) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {"token": svc.token, "closed": svc.closed}

    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="local",
            worker=WorkerConfig(placement="external"),
        )
    )
    app = Litestar(
        route_handlers=[dishka_queued, dishka_inproc],
        plugins=[queue_plugin, LitestarMCP()],
    )
    container = make_async_container(_RequestTraceProvider(lifecycle_events))
    setup_dishka(container, app)

    async with AsyncTestClient(app) as client:
        queued_resp = await _mcp_post(client, "tools/call", {"name": "dishka_queued", "arguments": {}})
        queued_task_id = queued_resp["result"]["taskId"]

        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)
        await worker.run_once()
        await worker.stop()

        queued_status = await _mcp_post(client, "tasks/get", {"taskId": queued_task_id})
        assert queued_status["result"]["status"] == "completed"
        queued_payload = json.loads(queued_status["result"]["result"]["content"][0]["text"])
        assert queued_payload["closed"] is False
        assert f"open:{queued_payload['token']}" in lifecycle_events
        assert f"close:{queued_payload['token']}" in lifecycle_events

        inproc_resp = await _mcp_post(client, "tools/call", {"name": "dishka_inproc", "arguments": {}})
        inproc_task_id = inproc_resp["result"]["taskId"]

        inproc_status: dict[str, Any] = {}
        for _ in range(40):
            inproc_status = await _mcp_post(client, "tasks/get", {"taskId": inproc_task_id})
            if inproc_status["result"]["status"] == "completed":
                break
            await asyncio.sleep(0.01)

        assert inproc_status["result"]["status"] == "completed"
        inproc_payload = json.loads(inproc_status["result"]["result"]["content"][0]["text"])
        assert inproc_payload["closed"] is False
        assert inproc_payload["token"] != queued_payload["token"]
        assert f"open:{inproc_payload['token']}" in lifecycle_events
        assert f"close:{inproc_payload['token']}" in lifecycle_events


@pytest.mark.anyio
async def test_litestar_security_principal_and_context_rehydration_in_worker() -> None:
    """Worker reconstructs Principal, SecurityContext, AuthorizationSnapshot, and CredentialRestrictions."""

    def require_security_scope(connection: ASGIConnection[Any, Any, Any, Any], _: Any) -> None:
        sec_ctx = cast("dict[str, Any]", connection.scope).get("litestar_security")
        if sec_ctx is None or "mcp:execute" not in sec_ctx.authorization.scopes:
            msg = "Missing required scope mcp:execute"
            raise PermissionError(msg)

    @get("/secure-audit", guards=[require_security_scope])
    @mcp_tool(name="secure_audit", task=True)
    async def secure_audit(request: Request[Any, Any, Any]) -> dict[str, Any]:
        sec_ctx = cast("dict[str, Any]", request.scope)["litestar_security"]
        return {
            "account_id": request.user.account_id,
            "user_email": request.user.user.email,
            "roles": sorted(sec_ctx.authorization.roles),
            "scopes": sorted(sec_ctx.authorization.scopes),
            "tenant_roles": {k: sorted(v) for k, v in sec_ctx.authorization.tenant_roles.items()},
            "restriction_tenants": sorted(sec_ctx.restrictions[0].tenant_ids or ()),
        }

    def security_context_middleware(app: Any) -> Any:
        async def asgi_handler(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                for name, value in scope.get("headers", []):
                    if name.lower() == b"x-security-principal":
                        acct = value.decode("utf-8")
                        restriction = _FallbackCredentialRestrictions(
                            scopes=frozenset({"mcp:execute"}),
                            tenant_ids=frozenset({"tenant-1"}),
                        )
                        principal = _FallbackPrincipal(
                            user={"id": acct, "email": f"{acct}@example.com"},
                            account_id=acct,
                            subject=acct,
                            mechanisms=("jwt",),
                            restrictions=(restriction,),
                            attributes=MappingProxyType({"idp": "google"}),
                        )
                        auth_snap = _FallbackAuthorizationSnapshot(
                            roles=frozenset({"operator"}),
                            permissions=frozenset({"tasks:run"}),
                            scopes=frozenset({"mcp:execute"}),
                            attributes=MappingProxyType({"region": "us"}),
                            tenant_roles=MappingProxyType({"tenant-1": frozenset({"admin"})}),
                        )
                        sec_ctx = _FallbackSecurityContext(
                            principal=principal,
                            authorization=auth_snap,
                            restrictions=(restriction,),
                        )
                        scope["user"] = principal
                        scope["auth"] = sec_ctx
                        scope["litestar_security"] = sec_ctx
                        break
            await app(scope, receive, send)

        return asgi_handler

    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="immediate",
            worker=WorkerConfig(placement="external"),
        )
    )
    app = Litestar(
        route_handlers=[secure_audit],
        plugins=[queue_plugin, LitestarMCP()],
        middleware=[security_context_middleware],
    )

    async with AsyncTestClient(app) as client:
        result = await _mcp_post(
            client,
            "tools/call",
            {"name": "secure_audit", "arguments": {}},
            headers={"X-Security-Principal": "acct-42"},
        )
        task_id = result["result"]["taskId"]
        status_result = await _mcp_post(
            client,
            "tasks/get",
            {"taskId": task_id},
            headers={"X-Security-Principal": "acct-42"},
        )
        assert status_result["result"]["status"] == "completed"
        assert json.loads(status_result["result"]["result"]["content"][0]["text"]) == {
            "account_id": "acct-42",
            "user_email": "acct-42@example.com",
            "roles": ["operator"],
            "scopes": ["mcp:execute"],
            "tenant_roles": {"tenant-1": ["admin"]},
            "restriction_tenants": ["tenant-1"],
        }


@pytest.mark.anyio
async def test_task_and_queue_override_hierarchy() -> None:
    """Verify plugin, controller, and route overrides for task mode and target queue lane."""
    queue_plugin_disabled = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="immediate",
            worker=WorkerConfig(placement="external"),
        )
    )
    mcp_disabled = LitestarMCP(MCPConfig(tasks=False))
    app_disabled = Litestar(route_handlers=[], plugins=[queue_plugin_disabled, mcp_disabled])
    assert app_disabled is not None
    assert mcp_disabled.config.task_config is None
    assert mcp_disabled.task_store is None

    queue_plugin_inproc = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="immediate",
            worker=WorkerConfig(placement="external"),
        )
    )
    mcp_inproc = LitestarMCP(MCPConfig(tasks=MCPTaskConfig(executor="in_process")))
    app_inproc = Litestar(route_handlers=[], plugins=[queue_plugin_inproc, mcp_inproc])
    assert app_inproc is not None
    assert mcp_inproc.config.task_config is not None
    assert mcp_inproc.config.task_config.executor is None

    class BatchController(Controller):
        path = "/batch"
        opt = {"mcp_task": True, "mcp_queue": "controller-lane"}

        @get("/inherited", mcp_tool="batch_inherited")
        async def inherited(self) -> dict[str, str]:
            return {"mode": "inherited"}

        @get("/sync-override")
        @mcp_tool(name="batch_sync", task="sync")
        async def sync_override(self) -> dict[str, str]:
            return {"mode": "sync"}

        @get("/custom-lane")
        @mcp_tool(name="batch_custom_lane", queue="route-lane")
        async def custom_lane(self) -> dict[str, str]:
            return {"mode": "route-lane"}

        @get("/inproc-override")
        @mcp_tool(name="batch_inproc", queue=False)
        async def inproc_override(self) -> dict[str, str]:
            return {"mode": "inproc"}

    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="immediate",
            worker=WorkerConfig(placement="external"),
        )
    )
    mcp_plugin = LitestarMCP()
    app = Litestar(route_handlers=[BatchController], plugins=[queue_plugin, mcp_plugin])

    async with AsyncTestClient(app) as client:
        sync_res = await _mcp_post(client, "tools/call", {"name": "batch_sync", "arguments": {}})
        assert sync_res["result"].get("resultType") != "task"
        assert json.loads(sync_res["result"]["content"][0]["text"]) == {"mode": "sync"}

        inh_res = await _mcp_post(client, "tools/call", {"name": "batch_inherited", "arguments": {}})
        assert inh_res["result"]["resultType"] == "task"
        inh_id = inh_res["result"]["taskId"]
        assert mcp_plugin.task_store is not None
        inh_dispatch = await mcp_plugin.task_store.get_dispatch(inh_id)
        assert inh_dispatch is not None
        assert inh_dispatch.queue == "controller-lane"

        lane_res = await _mcp_post(client, "tools/call", {"name": "batch_custom_lane", "arguments": {}})
        assert lane_res["result"]["resultType"] == "task"
        lane_id = lane_res["result"]["taskId"]
        lane_dispatch = await mcp_plugin.task_store.get_dispatch(lane_id)
        assert lane_dispatch is not None
        assert lane_dispatch.queue == "route-lane"

        inproc_res = await _mcp_post(client, "tools/call", {"name": "batch_inproc", "arguments": {}})
        assert inproc_res["result"]["resultType"] == "task"
        inproc_id = inproc_res["result"]["taskId"]
        inproc_status: dict[str, Any] = {}
        for _ in range(20):
            inproc_status = await _mcp_post(client, "tasks/get", {"taskId": inproc_id})
            if inproc_status["result"]["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert inproc_status["result"]["status"] == "completed"
        assert await mcp_plugin.task_store.get_dispatch(inproc_id) is None
        assert mcp_plugin.task_store.has_local_runner(inproc_id) is True


@pytest.mark.anyio
async def test_external_mcp_tool_meta_attribute_discovery() -> None:
    """Handlers decorated by external libraries setting _mcp_tool_meta are discovered and normalized."""

    async def external_tool(message: str = "ping") -> dict[str, str]:
        return {"reply": message}

    setattr(
        external_tool,
        "_mcp_tool_meta",
        {
            "name": "external_ping",
            "description": "External library tool",
            "task": "sync",
        },
    )
    route_handler = get("/external-ping")(external_tool)

    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="immediate",
            worker=WorkerConfig(placement="external"),
        )
    )
    app = Litestar(route_handlers=[route_handler], plugins=[queue_plugin, LitestarMCP()])

    async with AsyncTestClient(app) as client:
        tools_list = await _mcp_post(client, "tools/list", {})
        tool_names = [t["name"] for t in tools_list["result"]["tools"]]
        assert "external_ping" in tool_names

        call_res = await _mcp_post(
            client,
            "tools/call",
            {"name": "external_ping", "arguments": {"message": "pong"}},
        )
        assert call_res["result"].get("resultType") != "task"
        assert json.loads(call_res["result"]["content"][0]["text"]) == {"reply": "pong"}


async def _mcp_post_sse_or_json(
    client: AsyncTestClient[Litestar],
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_params = dict(params or {})
    meta = dict(request_params.get("_meta") or {})
    caps = dict(meta.get("io.modelcontextprotocol/clientCapabilities") or {})
    extensions = dict(caps.get("extensions") or {})
    extensions[TASKS_EXTENSION] = {}
    caps["extensions"] = extensions
    meta["io.modelcontextprotocol/clientCapabilities"] = caps
    meta["io.modelcontextprotocol/protocolVersion"] = "2026-07-28"
    request_params["_meta"] = meta

    body, req_headers = mcp_envelope(method, request_params)
    response = await client.post("/mcp", json=body, headers=req_headers)
    assert response.status_code == HTTP_200_OK
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        frames = [line for line in response.text.splitlines() if line.startswith("data:")]
        return cast("dict[str, Any]", from_json(frames[-1].removeprefix("data:")))
    return cast("dict[str, Any]", response.json())


@pytest.mark.anyio
async def test_queued_worker_progress_and_status_message() -> None:
    """Queued worker updates task statusMessage while working, hides internal fields, and clears statusMessage on completion."""
    step_gate = asyncio.Event()
    step_done = asyncio.Event()
    finish_gate = asyncio.Event()

    @get("/worker-progress")
    @mcp_tool(name="worker_progress", task_support="optional")
    async def worker_progress() -> dict[str, bool]:
        context = get_mcp_request_context()
        await step_gate.wait()
        await context.report_progress(1, total=2, message="Worker step 1")
        step_done.set()
        await finish_gate.wait()
        return {"ok": True}

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(
        executor=executor,
        execution_backend="local",
        extra_handlers=[worker_progress],
    )

    async with AsyncTestClient(app) as client:
        call_res = await _mcp_post_sse_or_json(
            client,
            "tools/call",
            {
                "name": "worker_progress",
                "arguments": {},
                "_meta": {"progressToken": "q-tok"},
            },
        )
        assert call_res["result"]["resultType"] == "task"
        task_id = call_res["result"]["taskId"]

        queue_plugin = app.plugins.get(QueuePlugin)
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)
        worker_task = asyncio.create_task(worker.run_once())
        try:
            step_gate.set()
            await asyncio.wait_for(step_done.wait(), timeout=2.0)

            working_res = await _mcp_post(client, "tasks/get", {"taskId": task_id})
            working_record = working_res["result"]
            assert working_record["status"] == "working"
            assert working_record["statusMessage"] == "Worker step 1"
            assert "progress_token" not in working_record
            assert "session_id" not in working_record
            assert "log_level" not in working_record
            assert "dispatchInternal" not in working_record

            finish_gate.set()
            await asyncio.wait_for(worker_task, timeout=2.0)
        finally:
            await worker.stop()

        final_res = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        final_record = final_res["result"]
        assert final_record["status"] == "completed"
        assert "statusMessage" not in final_record
        assert json.loads(final_record["result"]["content"][0]["text"]) == {"ok": True}


async def _wait_for_session_streams(plugin: LitestarMCP) -> None:
    for _ in range(400):
        if plugin.registry.has_subscription_manager and plugin.registry.subscription_manager._streams:
            return
        await asyncio.sleep(0.005)
    raise asyncio.TimeoutError


async def _read_next_sse_frame(lines: Any) -> dict[str, Any]:
    while True:
        line = await asyncio.wait_for(lines.__anext__(), timeout=2.0)
        if line.startswith("data:"):
            return cast("dict[str, Any]", from_json(line.removeprefix("data:").strip()))


async def _collect_cross_replica_events(session_events: asyncio.Queue[dict[str, Any]]) -> None:
    collected: list[dict[str, Any]] = []
    saw_progress = False
    saw_error_log = False
    while not (saw_progress and saw_error_log):
        event = await asyncio.wait_for(session_events.get(), timeout=2.0)
        collected.append(event)
        if event.get("method") == "notifications/progress":
            saw_progress = True
            assert event["params"] == {
                "progressToken": "rep-tok",
                "progress": 1,
                "total": 2,
                "message": "Phase A",
            }
        elif event.get("method") == "notifications/message":
            assert event["params"] == {
                "level": "error",
                "data": "keep",
                "logger": "worker",
            }
            saw_error_log = True

    assert all(
        not (e.get("method") == "notifications/message" and e.get("params", {}).get("data") == "drop")
        for e in collected
    )


def _build_cross_replica_apps() -> tuple[
    Litestar,
    QueuePlugin,
    LitestarMCP,
    Litestar,
    QueuePlugin,
]:
    shared_task_store = MemoryStore()
    shared_session_store = MemoryStore()
    channels = ChannelsPlugin(MemoryChannelsBackend(), channels=["litestar-mcp-subscriptions"])

    @get("/replica-work")
    @mcp_tool(name="replica_work", task_support="optional")
    async def web_replica_work() -> dict[str, bool]:
        return {"done": False}

    @get("/replica-work")
    @mcp_tool(name="replica_work", task_support="optional")
    async def worker_replica_work() -> dict[str, bool]:
        context = get_mcp_request_context()
        await context.report_progress(1, total=2, message="Phase A")
        await context.log("debug", "drop")
        await context.log("error", "keep", logger="worker")
        return {"done": True}

    worker_cfg = WorkerConfig(placement="external")
    web_qp = QueuePlugin(QueueConfig(queue_backend="memory", execution_backend="local", worker=worker_cfg))
    web_mcp = LitestarMCP(MCPConfig(tasks=MCPTaskConfig(executor=QueueTaskExecutor())))
    web_app = Litestar(
        route_handlers=[web_replica_work],
        plugins=[web_qp, channels, web_mcp],
        stores={TASK_STORE_NAME: shared_task_store, SESSION_STORE_NAME: shared_session_store},
    )

    worker_qp = QueuePlugin(QueueConfig(queue_backend="memory", execution_backend="local", worker=worker_cfg))
    worker_mcp = LitestarMCP(
        MCPConfig(subscription_channels=channels, tasks=MCPTaskConfig(executor=QueueTaskExecutor()))
    )
    worker_app = Litestar(
        route_handlers=[worker_replica_work],
        plugins=[worker_qp, worker_mcp],
        stores={TASK_STORE_NAME: shared_task_store, SESSION_STORE_NAME: shared_session_store},
    )
    return web_app, web_qp, web_mcp, worker_app, worker_qp


@pytest.mark.anyio
async def test_cross_replica_channels_session_sse_forwarding() -> None:
    """Worker progress and filtered logs forward across replicas via ChannelsPlugin to the owning session SSE stream."""
    web_app, web_qp, web_mcp, worker_app, worker_qp = _build_cross_replica_apps()

    async with web_app.lifespan():
        web_svc = web_qp.get_service(web_app.state)
        worker_svc = QueueService(worker_qp.config, queue_backend=web_svc.get_queue_backend())
        async with (
            worker_qp.standalone_app_lifespan(worker_app, service=worker_svc),
            MCPStreamableHTTPClient(
                "http://testserver.local/mcp",
                transport=ASGIStreamingTransport(web_app),
                protocol_version="2025-11-25",
                capabilities={"extensions": {TASKS_EXTENSION: {}}},
            ) as session_client,
        ):
            await session_client.initialize()
            assert session_client.session_id is not None
            await session_client.set_logging_level("warning")

            session_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            async def _read_session_sse() -> None:
                async for payload in session_client.open_event_stream():
                    await session_events.put(payload)

            sse_task = asyncio.create_task(_read_session_sse())
            try:
                await _wait_for_session_streams(web_mcp)

                listen_body, listen_headers = mcp_envelope(
                    "subscriptions/listen",
                    {"notifications": {"toolsListChanged": True}},
                    msg_id="listen-cross-replica",
                )
                async with (
                    httpx2.AsyncClient(
                        transport=ASGIStreamingTransport(web_app),
                        base_url="http://testserver.local",
                    ) as listen_http,
                    listen_http.stream("POST", "/mcp", json=listen_body, headers=listen_headers) as listen_resp,
                ):
                    assert listen_resp.status_code == HTTP_200_OK
                    lines = listen_resp.aiter_lines()
                    while (await _read_next_sse_frame(lines)).get(
                        "method"
                    ) != "notifications/subscriptions/acknowledged":
                        pass

                    task_res = await session_client.request(
                        "tools/call",
                        {
                            "name": "replica_work",
                            "arguments": {},
                            "_meta": {"progressToken": "rep-tok"},
                        },
                    )
                    assert task_res["resultType"] == "task"
                    task_id = task_res["taskId"]

                    worker = Worker(worker_svc, worker_qp.config.worker)
                    try:
                        await worker.run_once()
                    finally:
                        await worker.stop()

                    await _collect_cross_replica_events(session_events)

                    await web_mcp.registry.subscription_manager.publish("notifications/tools/list_changed", {})
                    frame = await _read_next_sse_frame(lines)
                    assert frame.get("method") == "notifications/tools/list_changed"

                    final_res = await session_client.request("tasks/get", {"taskId": task_id})
                    assert final_res["status"] == "completed"
                    assert json.loads(final_res["result"]["content"][0]["text"]) == {"done": True}
            finally:
                sse_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sse_task


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("channels_arg", "arbitrary_channels_allowed", "expected_wired"),
    [
        (["litestar-mcp-subscriptions"], False, True),
        (["unrelated"], False, False),
        (None, True, True),
    ],
)
async def test_bind_channels_plugin_compatibility_gate(
    channels_arg: list[str] | None,
    arbitrary_channels_allowed: bool,
    expected_wired: bool,
) -> None:
    """QueueTaskExecutor.bind adopts ChannelsPlugin only when it carries the subscription channel or allows arbitrary channels."""

    @get("/compat-echo")
    @mcp_tool(name="compat_echo", task_support="optional")
    async def compat_echo() -> dict[str, bool]:
        return {"ok": True}

    channels_plugin = ChannelsPlugin(
        MemoryChannelsBackend(),
        channels=channels_arg,
        arbitrary_channels_allowed=arbitrary_channels_allowed,
    )
    executor = QueueTaskExecutor()
    app = Litestar(
        route_handlers=[compat_echo],
        plugins=[
            QueuePlugin(
                QueueConfig(
                    queue_backend="memory",
                    execution_backend="local",
                    worker=WorkerConfig(placement="external"),
                )
            ),
            channels_plugin,
            mcp_plugin := LitestarMCP(MCPConfig(tasks=MCPTaskConfig(executor=executor))),
        ],
    )

    async with AsyncTestClient(app):
        assert mcp_plugin.registry.has_subscription_manager is True
        assert (mcp_plugin.registry.subscription_manager._channels is channels_plugin) is expected_wired


async def _assert_resumed_session_events(session_events: asyncio.Queue[dict[str, Any]]) -> None:
    received: list[dict[str, Any]] = []
    saw_progress = False
    saw_critical = False
    while not (saw_progress and saw_critical):
        event = await asyncio.wait_for(session_events.get(), timeout=2.0)
        received.append(event)
        if event.get("method") == "notifications/progress":
            saw_progress = True
            assert event["params"] == {
                "progressToken": "res-tok",
                "progress": 2,
                "total": 2,
                "message": "Resumed step 2",
            }
        elif event.get("method") == "notifications/message" and event["params"].get("data") == "done-crit":
            saw_critical = True
            assert event["params"]["level"] == "critical"

    assert all(
        not (e.get("method") == "notifications/message" and e.get("params", {}).get("data") == "skip-warn")
        for e in received
    )


@pytest.mark.anyio
async def test_queued_resume_preserves_progress_and_store_log_level() -> None:
    """Queued multi-round resume via tasks/update preserves progress_token and refreshes log_level from MCPSessionStore."""

    @get("/queued-resume")
    @mcp_tool(name="queued_resume", task_support="optional")
    async def queued_resume() -> MCPInputRequiredResult | dict[str, bool]:
        context = get_mcp_request_context()
        if not context.input_responses:
            return MCPInputRequiredResult(
                input_requests={"confirm": {"type": "boolean"}},
                request_state="round-1",
            )
        await context.report_progress(2, total=2, message="Resumed step 2")
        await context.log("warning", "skip-warn")
        await context.log("critical", "done-crit")
        return {"resumed": True}

    channels = ChannelsPlugin(MemoryChannelsBackend(), channels=["litestar-mcp-subscriptions"])
    mcp_plugin = LitestarMCP(MCPConfig(tasks=MCPTaskConfig(executor=QueueTaskExecutor())))
    queue_plugin = QueuePlugin(
        QueueConfig(
            queue_backend="memory",
            execution_backend="local",
            worker=WorkerConfig(placement="external"),
        )
    )
    app = Litestar(route_handlers=[queued_resume], plugins=[queue_plugin, channels, mcp_plugin])

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
        session_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _read_session_sse() -> None:
            async for payload in session_client.open_event_stream():
                await session_events.put(payload)

        sse_task = asyncio.create_task(_read_session_sse())
        try:
            await _wait_for_session_streams(mcp_plugin)

            task_res = await session_client.request(
                "tools/call",
                {
                    "name": "queued_resume",
                    "arguments": {},
                    "_meta": {"progressToken": "res-tok"},
                },
            )
            task_id = task_res["taskId"]

            service = queue_plugin.config.get_service(app.state)
            worker = Worker(service, queue_plugin.config.worker)
            try:
                await worker.run_once()
                first_status = await session_client.request("tasks/get", {"taskId": task_id})
                assert first_status["status"] == "input_required"

                await session_client.set_logging_level("error")
                mcp_plugin.registry.subscription_manager._session_log_levels.clear()

                await session_client.request(
                    "tasks/update",
                    {"taskId": task_id, "inputResponses": {"confirm": True}},
                )
                await worker.run_once()
            finally:
                await worker.stop()

            await _assert_resumed_session_events(session_events)

            final_status = await session_client.request("tasks/get", {"taskId": task_id})
            assert final_status["status"] == "completed"
            assert json.loads(final_status["result"]["content"][0]["text"]) == {"resumed": True}
        finally:
            sse_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sse_task


@pytest.mark.anyio
async def test_queued_missing_progress_token_fails_tool_cleanly() -> None:
    """Queued tool invoking report_progress without progressToken settles as completed isError=True."""

    @get("/queued-missing-tok")
    @mcp_tool(name="queued_missing_tok", task_support="optional")
    async def queued_missing_tok() -> dict[str, bool]:
        await get_mcp_request_context().report_progress(1)
        return {"ok": True}

    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(
        executor=executor,
        execution_backend="local",
        extra_handlers=[queued_missing_tok],
    )

    async with AsyncTestClient(app) as client:
        call_res = await _mcp_post(
            client,
            "tools/call",
            {"name": "queued_missing_tok", "arguments": {}},
        )
        task_id = call_res["result"]["taskId"]

        queue_plugin = app.plugins.get(QueuePlugin)
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)
        try:
            await worker.run_once()
        finally:
            await worker.stop()

        final_res = await _mcp_post(client, "tasks/get", {"taskId": task_id})
        final_record = final_res["result"]
        assert final_record["status"] == "completed"
        assert final_record["result"]["isError"] is True
        assert "did not supply a progress token" in final_record["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_queue_executor_validates_log_level_on_enqueue() -> None:
    """QueueTaskExecutor._enqueue_attempt rejects invalid log_level values with ValueError."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, execution_backend="local")

    async with AsyncTestClient(app):
        record = await executor.runtime.task_store.create("owner-1", local=False)
        invalid_call = ToolTaskCall(
            task_id=record.task_id,
            tool_name="slow_echo",
            arguments={"text": "x"},
            log_level="bogus-level",
        )
        with pytest.raises(ValueError, match="log level"):
            await executor.submit(record, invalid_call)
