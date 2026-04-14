"""Experimental MCP task support."""

import asyncio
import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from litestar_mcp.jsonrpc import INTERNAL_ERROR, JSONRPCError

TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        return int(raw)
    except (ValueError, binascii.Error) as exc:
        msg = "Invalid cursor"
        raise ValueError(msg) from exc


class TaskLookupError(ValueError):
    """Raised when a task cannot be found or accessed."""


class TaskStateError(ValueError):
    """Raised when a task transition is invalid."""


@dataclass
class TaskRecord:
    """In-memory representation of an MCP task."""

    task_id: str
    owner_id: str
    status: str
    created_at: datetime
    last_updated_at: datetime
    ttl: Optional[int]
    poll_interval: Optional[int]
    status_message: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[JSONRPCError] = None
    background_task: Optional[asyncio.Task[Any]] = field(default=None, repr=False)
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if self.ttl is None:
            return False
        current_time = now or _utc_now()
        return current_time >= self.created_at + timedelta(milliseconds=self.ttl)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "taskId": self.task_id,
            "status": self.status,
            "createdAt": _format_datetime(self.created_at),
            "lastUpdatedAt": _format_datetime(self.last_updated_at),
            "ttl": self.ttl,
        }
        if self.poll_interval is not None:
            payload["pollInterval"] = self.poll_interval
        if self.status_message is not None:
            payload["statusMessage"] = self.status_message
        return payload


class InMemoryTaskStore:
    """In-memory task storage for experimental MCP task support."""

    def __init__(
        self,
        default_ttl: int = 300_000,
        max_ttl: int = 3_600_000,
        poll_interval: int = 1_000,
        status_callback: "Callable[[TaskRecord], Awaitable[None]] | None" = None,
    ) -> None:
        self._default_ttl = default_ttl
        self._max_ttl = max_ttl
        self._poll_interval = poll_interval
        self._status_callback = status_callback
        self._lock = asyncio.Lock()
        self._tasks: dict[str, TaskRecord] = {}

    def set_status_callback(self, callback: "Callable[[TaskRecord], Awaitable[None]] | None") -> None:
        """Update the async callback used for task status notifications."""
        self._status_callback = callback

    async def create(
        self,
        owner_id: str,
        ttl: Optional[int],
        status_message: str = "The operation is now in progress.",
    ) -> TaskRecord:
        async with self._lock:
            self._purge_expired_locked()
            resolved_ttl = self._resolve_ttl(ttl)
            created_at = _utc_now()
            record = TaskRecord(
                task_id=str(uuid4()),
                owner_id=owner_id,
                status="working",
                created_at=created_at,
                last_updated_at=created_at,
                ttl=resolved_ttl,
                poll_interval=self._poll_interval,
                status_message=status_message,
            )
            self._tasks[record.task_id] = record

        await self._notify_status(record)
        return record

    async def attach_background_task(self, task_id: str, background_task: asyncio.Task[Any]) -> None:
        async with self._lock:
            record = self._lookup_locked(task_id)
            record.background_task = background_task

    async def get(self, task_id: str, owner_id: str) -> TaskRecord:
        async with self._lock:
            self._purge_expired_locked()
            record = self._lookup_locked(task_id, owner_id)
            return self._clone_record(record)

    async def list(self, owner_id: str, cursor: Optional[str] = None, limit: int = 50) -> tuple[list[TaskRecord], Optional[str]]:
        async with self._lock:
            self._purge_expired_locked()
            offset = _decode_cursor(cursor) if cursor is not None else 0
            owned_tasks = [self._clone_record(task) for task in self._tasks.values() if task.owner_id == owner_id]
            owned_tasks.sort(key=lambda item: item.created_at)
            page = owned_tasks[offset : offset + limit]
            next_cursor = None
            if offset + limit < len(owned_tasks):
                next_cursor = _encode_cursor(offset + limit)
            return page, next_cursor

    async def wait_for_terminal(self, task_id: str, owner_id: str) -> TaskRecord:
        record = await self.get(task_id, owner_id)
        if record.is_terminal():
            return record

        async with self._lock:
            live_record = self._lookup_locked(task_id, owner_id)
            done_event = live_record.done_event

        await done_event.wait()
        return await self.get(task_id, owner_id)

    async def complete(self, task_id: str, result: dict[str, Any]) -> TaskRecord:
        status = "failed" if result.get("isError") is True else "completed"
        status_message = None
        if status == "failed":
            status_message = result.get("content", [{}])[0].get("text")
        return await self.update_status(task_id, status=status, status_message=status_message, result=result)

    async def fail(
        self,
        task_id: str,
        error: JSONRPCError,
        status_message: Optional[str] = None,
    ) -> TaskRecord:
        return await self.update_status(
            task_id,
            status="failed",
            status_message=status_message or error.message,
            error=error,
        )

    async def cancel(self, task_id: str, owner_id: str) -> TaskRecord:
        async with self._lock:
            self._purge_expired_locked()
            record = self._lookup_locked(task_id, owner_id)
            if record.is_terminal():
                msg = f"Cannot cancel task: already in terminal status '{record.status}'"
                raise TaskStateError(msg)
            background_task = record.background_task

        if background_task is not None:
            background_task.cancel()

        return await self.update_status(
            task_id,
            status="cancelled",
            status_message="The task was cancelled by request.",
            error=JSONRPCError(code=INTERNAL_ERROR, message="Task was cancelled"),
        )

    async def update_status(
        self,
        task_id: str,
        *,
        status: str,
        status_message: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
        error: Optional[JSONRPCError] = None,
    ) -> TaskRecord:
        async with self._lock:
            record = self._lookup_locked(task_id)
            if record.is_terminal():
                return self._clone_record(record)

            record.status = status
            record.last_updated_at = _utc_now()
            record.status_message = status_message
            if result is not None:
                record.result = result
            if error is not None:
                record.error = error
            if status in TERMINAL_TASK_STATUSES:
                if record.result is None and record.error is None:
                    record.error = JSONRPCError(code=INTERNAL_ERROR, message="Task did not produce a final result")
                record.done_event.set()
            updated_record = self._clone_record(record)

        await self._notify_status(updated_record)
        return updated_record

    def _resolve_ttl(self, ttl: Optional[int]) -> Optional[int]:
        if ttl is None:
            return self._default_ttl
        if ttl <= 0:
            return 0
        return min(ttl, self._max_ttl)

    def _lookup_locked(self, task_id: str, owner_id: Optional[str] = None) -> TaskRecord:
        record = self._tasks.get(task_id)
        if record is None:
            msg = "Failed to retrieve task: Task not found"
            raise TaskLookupError(msg)
        if owner_id is not None and record.owner_id != owner_id:
            msg = "Failed to retrieve task: Task not found"
            raise TaskLookupError(msg)
        return record

    def _purge_expired_locked(self) -> None:
        now = _utc_now()
        expired_task_ids = [task_id for task_id, task in self._tasks.items() if task.is_expired(now)]
        for task_id in expired_task_ids:
            self._tasks.pop(task_id, None)

    async def _notify_status(self, record: TaskRecord) -> None:
        if self._status_callback is not None:
            await self._status_callback(record)

    def _clone_record(self, record: TaskRecord) -> TaskRecord:
        clone = TaskRecord(
            task_id=record.task_id,
            owner_id=record.owner_id,
            status=record.status,
            created_at=record.created_at,
            last_updated_at=record.last_updated_at,
            ttl=record.ttl,
            poll_interval=record.poll_interval,
            status_message=record.status_message,
            result=record.result,
            error=record.error,
        )
        if record.is_terminal():
            clone.done_event.set()
        return clone
