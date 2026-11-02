"""Unit tests for distributed MCP Tasks on litestar-queues via QueueTaskExecutor."""

import asyncio
from typing import Any, cast
from uuid import UUID

import pytest
from litestar import Litestar, Request, get
from litestar.connection import ASGIConnection
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
)
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

    plugins: list[Any] = []
    if include_queue_plugin and executor is not None:
        queue_config = QueueConfig(
            queue_backend="memory",
            execution_backend=execution_backend,
            worker=WorkerConfig(placement="external"),
            task_dependency_resolver=executor.dependencies(),
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
        route_handlers=[slow_echo, whoami, ask],
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
    assert response.status_code == HTTP_200_OK
    return cast("dict[str, Any]", response.json())


@pytest.mark.anyio
async def test_unbound_executor_raises() -> None:
    """QueueTaskExecutor.runtime raises RuntimeError before being bound to an app."""
    executor = QueueTaskExecutor()
    with pytest.raises(RuntimeError, match="QueueTaskExecutor is not bound"):
        _ = executor.runtime


@pytest.mark.anyio
async def test_missing_queue_plugin_raises() -> None:
    """App without QueuePlugin fails tools/call submission with ImproperlyConfiguredException."""
    executor = QueueTaskExecutor()
    app, _ = _make_queued_app(executor=executor, include_queue_plugin=False)
    async with AsyncTestClient(app) as client:
        response = await _mcp_post(
            client,
            "tools/call",
            {"name": "slow_echo", "arguments": {"text": "test"}},
        )
        assert "error" in response
        assert response["error"]["code"] == -32603  # INTERNAL_ERROR
        assert "QueuePlugin" in response["error"]["message"]


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
        assert status_result["result"]["status"] == "failed"
        assert "Authentication required" in status_result["result"]["error"]["message"]


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
