"""Task execution context injected into A2A skills."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from litestar_mcp.a2a.types import (
    Artifact,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    ThoughtPart,
)

if TYPE_CHECKING:
    from litestar_mcp.a2a.tasks import A2ATaskStore


class TaskContext:
    """Execution context injected into A2A skills for progress reporting and artifact streaming."""

    def __init__(
        self,
        task_id: str,
        session_id: str | None = None,
        task_store: "A2ATaskStore | None" = None,
        event_callback: Callable[[TaskStatusUpdateEvent | TaskArtifactUpdateEvent], Awaitable[None]] | None = None,
    ) -> None:
        self.task_id = task_id
        self.session_id = session_id
        self._task_store = task_store
        self._event_callback = event_callback
        self.emitted_artifacts: list[Artifact] = []

    async def thought(self, message: str) -> None:
        """Emit an internal thought or reasoning step."""
        thought_artifact = Artifact(
            name="thought",
            parts=[ThoughtPart(thought=message)],
        )
        self.emitted_artifacts.append(thought_artifact)
        if self._task_store is not None:
            await self._task_store.add_artifact(self.task_id, thought_artifact)

        if self._event_callback is not None:
            await self._event_callback(
                TaskStatusUpdateEvent(
                    task_id=self.task_id,
                    status=TaskStatus(state="working", message=message),
                    metadata={"thought": message},
                )
            )

    async def report_status(self, state: TaskState, message: str | None = None) -> None:
        """Report a status transition."""
        if self._task_store is not None:
            await self._task_store.update_task_status(self.task_id, state, message=message)

        if self._event_callback is not None:
            await self._event_callback(
                TaskStatusUpdateEvent(
                    task_id=self.task_id,
                    status=TaskStatus(state=state, message=message),
                )
            )

    async def emit_artifact(self, artifact: Artifact, final: bool = False) -> None:
        """Emit an output artifact."""
        self.emitted_artifacts.append(artifact)
        if self._task_store is not None:
            await self._task_store.add_artifact(self.task_id, artifact)

        if self._event_callback is not None:
            await self._event_callback(
                TaskArtifactUpdateEvent(
                    task_id=self.task_id,
                    artifact=artifact,
                    final=final,
                )
            )
