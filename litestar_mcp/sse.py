"""In-process SSE stream bookkeeping for MCP Streamable HTTP.

This module is deliberately narrow: it owns per-stream queues and a
Last-Event-ID replay buffer for clients that reconnect to ``GET /mcp``
after a network blip. Session identity and persistence live in
:mod:`litestar_mcp.sessions`; session ids are used here only as
in-process fan-out keys so a notification can be delivered to every
stream opened under the same ``Mcp-Session-Id``.

Resource caps:

- ``max_streams`` caps the total number of concurrent open streams.
  Exceeding it raises :class:`StreamLimitExceeded`, which the HTTP
  layer maps to ``503 Service Unavailable`` + JSON-RPC ``-32000``.
- ``max_idle_seconds`` prunes streams that have had no activity for
  longer than the window. Pruning is lazy: it runs on
  :meth:`SSEManager.open_stream` before admitting a new stream.
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from litestar.serialization import encode_json

__all__ = ("SSEManager", "SSEMessage", "StreamLimitExceeded")


class StreamLimitExceeded(Exception):
    """Raised by :meth:`SSEManager.open_stream` when ``max_streams`` is hit."""


@dataclass
class SSEMessage:
    """Represents a single SSE message."""

    data: str
    event: str = "message"
    id: str | None = None


@dataclass
class _StreamState:
    stream_id: str
    session_id: str | None
    queue: asyncio.Queue[SSEMessage] = field(default_factory=asyncio.Queue)
    history: list[SSEMessage] = field(default_factory=list)
    active: bool = True
    last_activity: float = field(default_factory=time.monotonic)


class SSEManager:
    """Manages Streamable HTTP SSE connections and message delivery.

    The manager keeps one :class:`_StreamState` per open stream and a
    side index from ``session_id`` to the set of stream ids currently
    attached to that session, so notifications can be fanned out to
    every stream belonging to a given MCP session.
    """

    def __init__(
        self,
        *,
        max_streams: int = 10_000,
        max_idle_seconds: float = 3600.0,
    ) -> None:
        """Initialize the SSE manager.

        Args:
            max_streams: Hard cap on concurrent open streams. Excess
                raises :class:`StreamLimitExceeded`.
            max_idle_seconds: Idle window (seconds) after which a stream
                is eligible for lazy pruning on the next ``open_stream``.
        """
        self._streams: dict[str, _StreamState] = {}
        self._session_streams: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()
        self._max_streams = max_streams
        self._max_idle_seconds = max_idle_seconds

    def attach_stream(self, session_id: str, stream_id: str) -> None:
        """Record an association between a session and a stream id.

        Called by the route layer after the session is validated and a
        stream is opened, so subsequent notifications can find every
        stream belonging to the session.
        """
        self._session_streams.setdefault(session_id, set()).add(stream_id)
        state = self._streams.get(stream_id)
        if state is not None:
            state.session_id = session_id

    def detach_stream(self, session_id: str, stream_id: str) -> None:
        """Remove the session→stream association. Idempotent."""
        streams = self._session_streams.get(session_id)
        if streams is None:
            return
        streams.discard(stream_id)
        if not streams:
            self._session_streams.pop(session_id, None)

    async def open_stream(
        self,
        session_id: str | None = None,
        last_event_id: str | None = None,
    ) -> tuple[str, AsyncGenerator[SSEMessage, None]]:
        """Open a new stream (or resume from ``last_event_id``).

        Args:
            session_id: Optional session id to bind this stream to. When
                provided, the manager updates the session→streams index
                so :meth:`publish` can fan out to all streams belonging
                to the session.
            last_event_id: Optional ``Last-Event-ID`` value. On a match,
                pending messages from the existing stream's history are
                replayed before normal delivery resumes.

        Returns:
            A ``(stream_id, async_generator)`` pair.

        Raises:
            StreamLimitExceeded: When admitting the new stream would
                exceed ``max_streams`` even after idle pruning.
        """
        async with self._lock:
            self._prune_idle_locked()
            state, replay_messages = self._get_or_create_stream_locked(session_id, last_event_id)

        async def stream() -> AsyncGenerator[SSEMessage, None]:
            try:
                for message in replay_messages:
                    yield message
                while True:
                    message = await state.queue.get()
                    state.last_activity = time.monotonic()
                    yield message
            finally:
                async with self._lock:
                    self._close_stream_locked(state.stream_id)

        return state.stream_id, stream()

    def disconnect(self, stream_id: str) -> None:
        """Explicitly remove a stream and its buffered state."""
        self._close_stream_locked(stream_id)

    async def enqueue(self, stream_id: str, message: dict[str, Any]) -> None:
        """Enqueue a raw JSON payload onto a single stream."""
        payload = encode_json(message).decode("utf-8")
        async with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                return
            sse_message = SSEMessage(data=payload, id=f"{stream_id}:{len(state.history)}")
            state.history.append(sse_message)
            state.last_activity = time.monotonic()
            await state.queue.put(sse_message)

    async def publish(self, message: dict[str, Any], session_id: str | None = None) -> None:
        """Publish a JSON payload to one or all sessions.

        When ``session_id`` is provided the message fans out to every
        stream attached to that session; otherwise it fans out to every
        stream attached to any session.
        """
        payload = encode_json(message).decode("utf-8")
        async with self._lock:
            if session_id is not None:
                target_stream_ids = list(self._session_streams.get(session_id, set()))
            else:
                target_stream_ids = [sid for ids in self._session_streams.values() for sid in ids]
            for stream_id in target_stream_ids:
                state = self._streams.get(stream_id)
                if state is None or not state.active:
                    continue
                sse_message = SSEMessage(data=payload, id=f"{stream_id}:{len(state.history)}")
                state.history.append(sse_message)
                state.last_activity = time.monotonic()
                await state.queue.put(sse_message)

    async def replay_from(self, stream_id: str, last_event_id: str) -> list[SSEMessage]:
        """Return buffered messages after ``last_event_id`` for a stream."""
        async with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                return []
            _, event_index = self._parse_event_id(last_event_id)
            state.last_activity = time.monotonic()
            return list(state.history[event_index + 1 :])

    def close_session_streams(self, session_id: str) -> list[str]:
        """Close every stream attached to ``session_id``. Returns closed ids."""
        stream_ids = list(self._session_streams.get(session_id, set()))
        for stream_id in stream_ids:
            self._close_stream_locked(stream_id)
        self._session_streams.pop(session_id, None)
        return stream_ids

    def _prune_idle_locked(self) -> None:
        if self._max_idle_seconds <= 0:
            return
        cutoff = time.monotonic() - self._max_idle_seconds
        to_remove = [sid for sid, state in self._streams.items() if state.last_activity < cutoff]
        for stream_id in to_remove:
            self._close_stream_locked(stream_id)

    def _get_or_create_stream_locked(
        self,
        session_id: str | None,
        last_event_id: str | None,
    ) -> tuple[_StreamState, list[SSEMessage]]:
        if last_event_id:
            try:
                stream_id, event_index = self._parse_event_id(last_event_id)
            except ValueError:
                stream_id, event_index = None, -1
            if stream_id is not None:
                existing = self._streams.get(stream_id)
                if existing is not None and (session_id is None or existing.session_id == session_id):
                    existing.active = True
                    existing.last_activity = time.monotonic()
                    if session_id is not None:
                        self._session_streams.setdefault(session_id, set()).add(stream_id)
                    replay_messages = existing.history[event_index + 1 :]
                    return existing, replay_messages

        if len(self._streams) >= self._max_streams:
            msg = f"SSE stream limit exceeded (max_streams={self._max_streams})"
            raise StreamLimitExceeded(msg)

        stream_id = str(uuid4())
        state = _StreamState(stream_id=stream_id, session_id=session_id)
        prime_message = SSEMessage(data="", id=f"{stream_id}:0")
        state.history.append(prime_message)
        state.queue.put_nowait(prime_message)
        self._streams[stream_id] = state
        if session_id is not None:
            self._session_streams.setdefault(session_id, set()).add(stream_id)
        return state, []

    def _close_stream_locked(self, stream_id: str) -> None:
        state = self._streams.pop(stream_id, None)
        if state is None:
            return
        state.active = False
        if state.session_id is not None:
            streams = self._session_streams.get(state.session_id)
            if streams is not None:
                streams.discard(stream_id)
                if not streams:
                    self._session_streams.pop(state.session_id, None)

    @staticmethod
    def _parse_event_id(value: str) -> tuple[str, int]:
        stream_id, _, raw_index = value.rpartition(":")
        if not stream_id:
            msg = "Invalid Last-Event-ID header"
            raise ValueError(msg)
        return stream_id, int(raw_index)
