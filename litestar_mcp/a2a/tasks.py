"""Task storage and state persistence for A2A tasks."""

from datetime import datetime, timezone

import msgspec
from litestar.stores.base import Store
from litestar.stores.memory import MemoryStore

from litestar_mcp.a2a.types import (
    Artifact,
    Task,
    TaskState,
    TaskStatus,
)


class TaskLookupError(Exception):
    """Raised when a task ID cannot be found in the store."""


class A2ATaskStore:
    """Task storage backend for persisting and querying A2A tasks."""

    def __init__(
        self,
        store: Store,
        default_ttl_ms: int = 3_600_000,
        key_prefix: str = "a2a_task",
    ) -> None:
        self._store = store
        self._default_ttl_ms = default_ttl_ms
        self._key_prefix = key_prefix

    def _key(self, task_id: str) -> str:
        return f"{self._key_prefix}:{task_id}"

    async def save_task(self, task: Task) -> None:
        """Persist a task in the store."""
        encoded = msgspec.json.encode(task)
        await self._store.set(self._key(task.id), encoded, expires_in=self._default_ttl_ms // 1000)

    async def get_task(self, task_id: str) -> Task:
        """Retrieve a task by ID."""
        raw = await self._store.get(self._key(task_id))
        if raw is None:
            msg = f"Task {task_id!r} not found"
            raise TaskLookupError(msg)
        return msgspec.json.decode(raw, type=Task)

    async def update_task_status(
        self,
        task_id: str,
        state: TaskState,
        message: str | None = None,
    ) -> Task:
        """Update a task's status and message."""
        task = await self.get_task(task_id)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        task.status = TaskStatus(state=state, message=message, timestamp=now_iso)
        await self.save_task(task)
        return task

    async def add_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """Append an artifact to a task."""
        task = await self.get_task(task_id)
        task.artifacts.append(artifact)
        await self.save_task(task)
        return task


class A2AMemoryTaskStore(A2ATaskStore):
    """In-memory default task store for A2A."""

    def __init__(self, default_ttl_ms: int = 3_600_000, key_prefix: str = "a2a_task") -> None:
        super().__init__(
            store=MemoryStore(),
            default_ttl_ms=default_ttl_ms,
            key_prefix=key_prefix,
        )
