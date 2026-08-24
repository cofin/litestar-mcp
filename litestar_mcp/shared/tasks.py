"""Durable TaskStore interfaces and task record abstractions."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from litestar.serialization import decode_json, encode_json
from litestar.stores.base import Store
from litestar.stores.memory import MemoryStore

from litestar_mcp.shared.jsonrpc import JSONRPCError


class TaskLookupError(ValueError):
    """Raised when a task cannot be found or accessed."""


class TaskStateError(ValueError):
    """Raised when a task transition is invalid."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class TaskRecord:
    """Persisted task state record."""

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

    def is_terminal(self, terminal_statuses: frozenset[str]) -> bool:
        """Return whether the task has reached a terminal state."""
        return self.status in terminal_statuses

    def to_dict(self) -> dict[str, Any]:
        """Return the dictionary wire representation."""
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


class BaseTaskStore:
    """Base TaskStore backed by a Litestar Store."""

    def __init__(
        self,
        store: Store | None = None,
        default_ttl_ms: int = 300_000,
        max_ttl_ms: int = 3_600_000,
        poll_interval_ms: int = 1_000,
        status_callback: Callable[[TaskRecord], Awaitable[None]] | None = None,
        key_prefix: str = "task",
    ) -> None:
        self.store = store or MemoryStore()
        self.default_ttl_ms = default_ttl_ms
        self.max_ttl_ms = max_ttl_ms
        self.poll_interval_ms = poll_interval_ms
        self.status_callback = status_callback
        self.key_prefix = key_prefix
        self._lock = asyncio.Lock()
        self._runners: dict[str, asyncio.Task[Any]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def _key(self, task_id: str) -> str:
        return f"{self.key_prefix}:{task_id}"

    async def get(self, task_id: str, owner_id: str | None = None) -> TaskRecord:
        """Fetch a record by id."""
        data = await self.store.get(self._key(task_id))
        if data is None:
            msg = f"Task {task_id!r} not found"
            raise TaskLookupError(msg)
        record = _decode_record(data)
        if owner_id is not None and record.owner_id is not None and record.owner_id != owner_id:
            msg = f"Task {task_id!r} not accessible by caller"
            raise TaskLookupError(msg)
        return record

    async def _persist(self, record: TaskRecord) -> None:
        record.last_updated_at = _utc_now()
        ttl_seconds = (record.ttl_ms // 1000) if record.ttl_ms else (self.default_ttl_ms // 1000)
        await self.store.set(self._key(record.task_id), _encode_record(record), expires_in=ttl_seconds)
