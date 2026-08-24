"""Generic Server-Sent Events (SSE) stream subscription and queue management."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

_CLOSED = object()


class StreamLimitExceeded(Exception):  # noqa: N818
    """Raised when the configured subscription stream cap is reached."""


@dataclass
class StreamSubscription:
    """Individual client subscription stream state."""

    stream_id: str
    subscription_id: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    queue: asyncio.Queue[dict[str, Any] | object] = field(default_factory=asyncio.Queue)


class BaseSubscriptionManager:
    """Base manager for concurrent SSE stream queues and disconnect lifecycles."""

    def __init__(self, *, max_streams: int = 10_000, channels: Any | None = None) -> None:
        self._max_streams = max_streams
        self._channels = channels
        self._streams: dict[str, StreamSubscription] = {}
        self._lock = asyncio.Lock()
        self._broker_task: asyncio.Task[None] | None = None

    async def open_stream(
        self,
        subscription_id: Any,
        metadata: dict[str, Any] | None = None,
        initial_message: dict[str, Any] | None = None,
    ) -> tuple[str, AsyncGenerator[dict[str, Any], None]]:
        """Open a new stream queue and return its ID and async generator."""
        async with self._lock:
            if len(self._streams) >= self._max_streams:
                msg = f"Subscription stream limit exceeded (max_streams={self._max_streams})"
                raise StreamLimitExceeded(msg)
            stream_id = str(uuid4())
            sub = StreamSubscription(
                stream_id=stream_id,
                subscription_id=subscription_id,
                metadata=metadata or {},
            )
            self._streams[stream_id] = sub
            if initial_message is not None:
                sub.queue.put_nowait(initial_message)

        async def stream() -> AsyncGenerator[dict[str, Any], None]:
            try:
                while True:
                    message = await sub.queue.get()
                    if message is _CLOSED:
                        return
                    yield message  # type: ignore[misc]
            finally:
                await self.disconnect(stream_id)

        return stream_id, stream()

    async def disconnect(self, stream_id: str) -> None:
        """Disconnect a single stream queue and signal its consumer."""
        async with self._lock:
            sub = self._streams.pop(stream_id, None)
        if sub is not None:
            sub.queue.put_nowait(_CLOSED)

    async def close_all(self) -> None:
        """Close all active streams and stop any background broker task."""
        if self._broker_task is not None:
            self._broker_task.cancel()
            await asyncio.gather(self._broker_task, return_exceptions=True)
            self._broker_task = None
        async with self._lock:
            subs = tuple(self._streams.values())
            self._streams.clear()
        for sub in subs:
            sub.queue.put_nowait(_CLOSED)
