"""Server-Sent Events (SSE) utilities for MCP transport.

This module provides SSE formatting, heartbeat keep-alive, and backpressure
handling for streaming MCP tool responses.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any, Optional

from litestar.serialization import encode_json

__all__ = ("format_sse_event", "sse_heartbeat", "stream_with_backpressure", "stream_with_heartbeat")


def format_sse_event(event_type: str, data: "dict[str, Any]") -> str:
    r"""Format data as Server-Sent Events (SSE) message.

    Args:
        event_type: SSE event type (e.g., "result", "progress", "done")
        data: Data payload to send as JSON

    Returns:
        SSE-formatted string with event type and JSON data

    Example:
        >>> format_sse_event("result", {"status": "ok"})
        'event: result\\ndata: {"status":"ok"}\\n\\n'
    """
    json_data = encode_json(data).decode("utf-8")
    return f"event: {event_type}\ndata: {json_data}\n\n"


async def sse_heartbeat(interval: float = 30) -> AsyncGenerator[str, None]:
    r"""Send periodic heartbeat comments to keep SSE connection alive.

    SSE comments (lines starting with ":") are ignored by clients but prevent
    proxies and load balancers from timing out idle connections.

    Args:
        interval: Heartbeat interval in seconds (default: 30)

    Yields:
        SSE comment strings (": heartbeat\\n\\n")

    Example:
        >>> async for heartbeat in sse_heartbeat(interval=15):
        ...     print(heartbeat)
        : heartbeat

    """
    while True:
        await asyncio.sleep(interval)
        yield ": heartbeat\n\n"


async def stream_with_heartbeat(
    data_stream: AsyncGenerator["dict[str, Any]", None],
    heartbeat_interval: float = 30,
) -> AsyncGenerator[str, None]:
    """Merge data stream with heartbeat keep-alive messages.

    Combines actual data events with periodic heartbeat comments to prevent
    connection timeouts during long-running operations.

    Args:
        data_stream: Async generator yielding MCP response dicts
        heartbeat_interval: Heartbeat interval in seconds (default: 30)

    Yields:
        SSE-formatted event strings (data events + heartbeat comments)

    Example:
        >>> async def data_gen():
        ...     yield {"result": "chunk1"}
        ...     yield {"result": "chunk2"}
        >>> async for event in stream_with_heartbeat(data_gen()):
        ...     print(event)
    """
    heartbeat_gen = sse_heartbeat(heartbeat_interval)
    data_iter = data_stream.__aiter__()

    async def next_heartbeat() -> str:
        return await heartbeat_gen.__anext__()

    async def next_data() -> dict[str, Any]:
        return await data_iter.__anext__()

    heartbeat_task: Optional[asyncio.Task[str]] = asyncio.create_task(next_heartbeat())
    data_task: Optional[asyncio.Task[dict[str, Any]]] = asyncio.create_task(next_data())

    try:
        while data_task is not None and heartbeat_task is not None:
            done, _pending = await asyncio.wait(
                {data_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if data_task in done:
                try:
                    data = data_task.result()
                except StopAsyncIteration:
                    data_task = None
                else:
                    yield format_sse_event("result", data)
                    data_task = asyncio.create_task(next_data())

            if heartbeat_task in done:
                try:
                    yield heartbeat_task.result()
                except StopAsyncIteration:
                    heartbeat_task = None
                else:
                    heartbeat_task = asyncio.create_task(next_heartbeat())

        yield format_sse_event("done", {})

    finally:
        if data_task:
            data_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await data_task
        if heartbeat_task:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await heartbeat_task
        with contextlib.suppress(Exception):
            await data_stream.aclose()
        with contextlib.suppress(Exception):
            await heartbeat_gen.aclose()


async def stream_with_backpressure(
    data_stream: AsyncGenerator["dict[str, Any]", None],
    batch_size: int = 10,
    flush_interval: float = 1.0,
) -> AsyncGenerator[str, None]:
    """Batch events to handle slow clients and prevent memory exhaustion.

    Accumulates events into batches and flushes when either batch_size is reached
    or flush_interval has elapsed, whichever comes first.

    Args:
        data_stream: Source data stream
        batch_size: Maximum events per batch (default: 10)
        flush_interval: Force flush after interval in seconds (default: 1.0)

    Yields:
        Batched SSE events

    Example:
        >>> async def data_gen():
        ...     for i in range(100):
        ...         yield {"value": i}
        >>> async for batch_event in stream_with_backpressure(data_gen(), batch_size=5):
        ...     print(batch_event)
    """
    batch: list[dict[str, Any]] = []
    last_flush = asyncio.get_event_loop().time()

    try:
        async for data in data_stream:
            batch.append(data)

            now = asyncio.get_event_loop().time()
            should_flush = len(batch) >= batch_size or (now - last_flush) >= flush_interval

            if should_flush:
                yield format_sse_event("batch", {"items": batch})
                batch.clear()
                last_flush = now

        if batch:
            yield format_sse_event("batch", {"items": batch})

        yield format_sse_event("done", {})

    finally:
        batch.clear()
