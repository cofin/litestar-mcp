"""Real-time SSE streaming adapter and event framing for A2A."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from litestar.serialization import decode_json, encode_json

from litestar_mcp.a2a.types import (
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from litestar_mcp.core import BaseSubscriptionManager


def format_a2a_sse_event(event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent) -> dict[str, Any]:
    """Frame an A2A streaming event inside a standard JSON-RPC 2.0 envelope."""
    if isinstance(event, TaskStatusUpdateEvent):
        return {
            "jsonrpc": "2.0",
            "method": "tasks/statusUpdate",
            "params": decode_json(encode_json(event)),
        }
    return {
        "jsonrpc": "2.0",
        "method": "tasks/artifactUpdate",
        "params": decode_json(encode_json(event)),
    }


class A2ASubscriptionManager(BaseSubscriptionManager):
    """Manages Server-Sent Events queues and broadcasts for A2A tasks."""

    def __init__(self, max_streams: int = 100) -> None:
        super().__init__(max_streams=max_streams)
        self._task_subscriptions: dict[str, set[str]] = {}

    async def open_a2a_stream(
        self,
        task_id: str,
        initial_message: dict[str, Any] | None = None,
    ) -> tuple[str, AsyncGenerator[dict[str, Any], None]]:
        """Open a streaming connection dedicated to a specific task."""
        sub_id, generator = await self.open_stream(subscription_id=task_id, initial_message=initial_message)
        self._task_subscriptions.setdefault(task_id, set()).add(sub_id)
        return sub_id, generator

    async def publish_event(self, event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent) -> None:
        """Broadcast formatted event to all streams listening to the task."""
        payload = format_a2a_sse_event(event)
        sub_ids = self._task_subscriptions.get(event.task_id, set())

        dead_ids: list[str] = []
        for sub_id in sub_ids:
            sub = self._streams.get(sub_id)
            if sub is not None:
                with contextlib.suppress(asyncio.QueueFull):
                    sub.queue.put_nowait(payload)
            else:
                dead_ids.append(sub_id)

        for dead_id in dead_ids:
            sub_ids.discard(dead_id)

    async def disconnect(self, subscription_id: str) -> None:
        """Disconnect and clean up task subscription index."""
        await super().disconnect(subscription_id)
        for sub_ids in self._task_subscriptions.values():
            sub_ids.discard(subscription_id)
