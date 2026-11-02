"""Unit tests for distributed MCP Tasks on litestar-queues via QueueTaskExecutor."""

import asyncio
from typing import Any, cast
from uuid import UUID

import anyio
import pytest

pytest.importorskip("litestar_queues")

from litestar import Litestar, Request, get
from litestar.connection import ASGIConnection
from litestar.exceptions import ImproperlyConfiguredException
from litestar.status_codes import HTTP_200_OK
from litestar.testing import AsyncTestClient
from litestar_queues import QueueConfig, QueuePlugin
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
from litestar_mcp.mcp.service import TASKS_EXTENSION
from litestar_mcp.queues import QueueTaskExecutor
from litestar_mcp.utils import mcp_tool


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
    from tests.unit.conftest import mcp_envelope

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
