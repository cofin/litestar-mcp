"""Durable support for the ``io.modelcontextprotocol/tasks`` extension."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from litestar.serialization import decode_json, encode_json
from litestar.stores.base import Store
from litestar.stores.memory import MemoryStore

from litestar_mcp.jsonrpc import JSONRPCError

TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


class TaskLookupError(ValueError):
    """Raised when a task cannot be found or accessed."""


class TaskStateError(ValueError):
    """Raised when a task transition is invalid."""


@dataclass
class TaskRecord:
    """Persisted Tasks extension state."""

    task_id: str
    owner_id: str | None
    status: str
    created_at: datetime
    last_updated_at: datetime
    ttl_ms: int | None
    poll_interval_ms: int | None
    status_message: str | None = None
    input_requests: dict[str, dict[str, Any]] | None = None
    request_state: str | None = None
    result: dict[str, Any] | None = None
    error: JSONRPCError | None = None

    def is_terminal(self) -> bool:
        """Return whether the task has reached a terminal state."""
        return self.status in TERMINAL_TASK_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """Return the extension wire representation."""
        payload: dict[str, Any] = {
            "taskId": self.task_id,
            "status": self.status,
            "createdAt": _format_datetime(self.created_at),
            "lastUpdatedAt": _format_datetime(self.last_updated_at),
            "ttlMs": self.ttl_ms,
        }
        if self.poll_interval_ms is not None:
            payload["pollIntervalMs"] = self.poll_interval_ms
        if self.status_message is not None:
            payload["statusMessage"] = self.status_message
        if self.input_requests is not None:
            payload["inputRequests"] = self.input_requests
        if self.result is not None:
            payload["result"] = self.result
        if self.error is not None:
            payload["error"] = {
                "code": self.error.code,
                "message": self.error.message,
                **({"data": self.error.data} if self.error.data is not None else {}),
            }
        return payload


class MCPTaskStore:
    """Task records backed by a Litestar :class:`Store`.

    Only serializable records are persisted. Runner tasks, cancellation flags,
    and input queues are process-local coordination primitives; production
    deployments use a shared Store for retrieval and a shared notification
    backend for fan-out.
    """

    def __init__(
        self,
        store: Store | None = None,
        default_ttl_ms: int = 300_000,
        max_ttl_ms: int = 3_600_000,
        poll_interval_ms: int = 1_000,
        status_callback: Callable[[TaskRecord], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store or MemoryStore()
        self.default_ttl_ms = default_ttl_ms
        self.max_ttl_ms = max_ttl_ms
        self.poll_interval_ms = poll_interval_ms
        self.status_callback = status_callback
        self._lock = asyncio.Lock()
        self._runners: dict[str, asyncio.Task[Any]] = {}
        self._input_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._input_responses: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def set_status_callback(self, callback: Callable[[TaskRecord], Awaitable[None]] | None) -> None:
        """Set the task-state notification callback."""
        self.status_callback = callback

    async def create(self, owner_id: str | None, ttl_ms: int | None = None) -> TaskRecord:
        """Durably create a task before returning its handle."""
        resolved_ttl = self._resolve_ttl(ttl_ms)
        now = _utc_now()
        record = TaskRecord(
            task_id=uuid4().hex,
            owner_id=owner_id,
            status="working",
            created_at=now,
            last_updated_at=now,
            ttl_ms=resolved_ttl,
            poll_interval_ms=self.poll_interval_ms,
            status_message="The operation is now in progress.",
        )
        async with self._lock:
            self._input_queues[record.task_id] = asyncio.Queue()
            self._cancel_events[record.task_id] = asyncio.Event()
            await self._save(record)
        await self._notify(record)
        return record

    async def attach_runner(self, task_id: str, runner: asyncio.Task[Any]) -> None:
        """Attach a process-local runner to a durable task."""
        async with self._lock:
            await self._lookup(task_id, None)
            self._runners[task_id] = runner

    async def get(self, task_id: str, owner_id: str | None) -> TaskRecord:
        """Retrieve a task, enforcing authenticated ownership when present."""
        async with self._lock:
            return await self._lookup(task_id, owner_id)

    async def complete(self, task_id: str, result: dict[str, Any]) -> TaskRecord:
        """Persist a successful tool result."""
        return await self._transition(task_id, "completed", result=result, status_message=None)

    async def fail(self, task_id: str, error: JSONRPCError, status_message: str | None = None) -> TaskRecord:
        """Persist a failed task."""
        return await self._transition(task_id, "failed", error=error, status_message=status_message or error.message)

    async def require_input(
        self,
        task_id: str,
        input_requests: dict[str, dict[str, Any]] | None,
        request_state: str | None,
    ) -> TaskRecord:
        """Pause a task until the client supplies input via ``tasks/update``."""
        return await self._transition(
            task_id,
            "input_required",
            input_requests=input_requests or {},
            request_state=request_state,
            status_message="Additional client input is required.",
        )

    async def wait_for_input(self, task_id: str) -> dict[str, Any]:
        """Wait for the next input response or cancellation request."""
        queue = self._input_queues.setdefault(task_id, asyncio.Queue())
        cancel_event = self._cancel_events.setdefault(task_id, asyncio.Event())
        input_wait = asyncio.create_task(queue.get())
        cancel_wait = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait({input_wait, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
        for waiter in pending:
            waiter.cancel()
        if cancel_wait in done:
            raise asyncio.CancelledError
        return input_wait.result()

    async def update(self, task_id: str, owner_id: str | None, input_responses: dict[str, Any]) -> None:
        """Submit client responses to a task waiting for input."""
        should_resume = False
        merged: dict[str, Any] = {}
        async with self._lock:
            record = await self._lookup(task_id, owner_id)
            if record.status != "input_required":
                return
            outstanding = record.input_requests or {}
            accepted = {key: value for key, value in input_responses.items() if key in outstanding}
            if not accepted:
                return
            merged = self._input_responses.setdefault(task_id, {})
            merged.update(accepted)
            remaining = {key: value for key, value in outstanding.items() if key not in accepted}
            record.last_updated_at = _utc_now()
            if remaining:
                record.input_requests = remaining
                record.status_message = "Additional client input is required."
            else:
                record.status = "working"
                record.input_requests = None
                record.status_message = "Client input received; processing resumed."
                should_resume = True
            await self._save(record)
        await self._notify(record)
        if should_resume:
            responses = dict(merged)
            self._input_responses.pop(task_id, None)
            await self._input_queues.setdefault(task_id, asyncio.Queue()).put(responses)

    async def cancel(self, task_id: str, owner_id: str | None) -> None:
        """Request cooperative cancellation and acknowledge immediately."""
        record = await self.get(task_id, owner_id)
        if record.is_terminal():
            return
        self._cancel_events.setdefault(task_id, asyncio.Event()).set()
        runner = self._runners.get(task_id)
        if runner is not None:
            runner.cancel()

    async def mark_cancelled(self, task_id: str) -> TaskRecord:
        """Persist cancellation after the runner cooperates."""
        return await self._transition(task_id, "cancelled", status_message="The task was cancelled.")

    async def close(self) -> None:
        """Cancel all process-local runners during graceful shutdown."""
        runners = list(self._runners.values())
        for runner in runners:
            runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)

    async def _transition(
        self,
        task_id: str,
        status: str,
        *,
        status_message: str | None = None,
        input_requests: dict[str, dict[str, Any]] | None = None,
        request_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: JSONRPCError | None = None,
    ) -> TaskRecord:
        async with self._lock:
            record = await self._lookup(task_id, None)
            if record.is_terminal():
                return record
            record.status = status
            record.last_updated_at = _utc_now()
            record.status_message = status_message
            record.input_requests = input_requests
            record.request_state = request_state
            record.result = result
            record.error = error
            await self._save(record)
        await self._notify(record)
        return record

    async def _lookup(self, task_id: str, owner_id: str | None) -> TaskRecord:
        value = await self.store.get(self._key(task_id))
        if value is None:
            msg = "Failed to retrieve task: Task not found"
            raise TaskLookupError(msg)
        record = _decode_record(value)
        if owner_id is not None and record.owner_id is not None and record.owner_id != owner_id:
            msg = "Failed to retrieve task: Task not found"
            raise TaskLookupError(msg)
        return record

    async def _save(self, record: TaskRecord) -> None:
        expires_in = None if record.ttl_ms is None else max(1, (record.ttl_ms + 999) // 1000)
        await self.store.set(self._key(record.task_id), _encode_record(record), expires_in=expires_in)

    async def _notify(self, record: TaskRecord) -> None:
        if self.status_callback is not None:
            await self.status_callback(record)

    def _resolve_ttl(self, ttl_ms: int | None) -> int | None:
        if ttl_ms is None:
            return self.default_ttl_ms
        if ttl_ms < 0:
            msg = "ttlMs must be non-negative or null"
            raise TaskStateError(msg)
        return min(ttl_ms, self.max_ttl_ms)

    @staticmethod
    def _key(task_id: str) -> str:
        return f"mcp-task:{task_id}"


# Transitional source alias; the wire protocol has no legacy task surface.
InMemoryTaskStore = MCPTaskStore


def _encode_record(record: TaskRecord) -> bytes:
    payload = record.to_dict()
    payload["ownerId"] = record.owner_id
    payload["requestStateInternal"] = record.request_state
    return encode_json(payload)


def _decode_record(value: bytes) -> TaskRecord:
    payload = decode_json(value)
    error_payload = payload.get("error")
    return TaskRecord(
        task_id=payload["taskId"],
        owner_id=payload.get("ownerId"),
        status=payload["status"],
        created_at=_parse_datetime(payload["createdAt"]),
        last_updated_at=_parse_datetime(payload["lastUpdatedAt"]),
        ttl_ms=payload.get("ttlMs"),
        poll_interval_ms=payload.get("pollIntervalMs"),
        status_message=payload.get("statusMessage"),
        input_requests=payload.get("inputRequests"),
        request_state=payload.get("requestStateInternal"),
        result=payload.get("result"),
        error=(
            JSONRPCError(
                code=error_payload["code"],
                message=error_payload["message"],
                data=error_payload.get("data"),
            )
            if error_payload is not None
            else None
        ),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
