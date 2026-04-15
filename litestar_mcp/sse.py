"""SSE transport management for MCP."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from litestar.serialization import encode_json


@dataclass
class SSEMessage:
    """Represents a single SSE message."""

    data: str
    event: str = "message"
    id: str | None = None


@dataclass
class _StreamState:
    stream_id: str
    client_id: str
    queue: asyncio.Queue[SSEMessage] = field(default_factory=asyncio.Queue)
    history: list[SSEMessage] = field(default_factory=list)
    active: bool = True


@dataclass
class _ClientGroup:
    stream_ids: list[str] = field(default_factory=list)
    next_index: int = 0


class SSEManager:
    """Manages Streamable HTTP SSE connections and message delivery."""

    def __init__(self) -> None:
        self._client_groups: dict[str, _ClientGroup] = {}
        self._streams: dict[str, _StreamState] = {}
        self._lock = asyncio.Lock()

    def register_client(self, client_id: str) -> None:
        """Ensure a client group exists."""
        self._client_groups.setdefault(client_id, _ClientGroup())

    async def open_stream(
        self,
        client_id: str,
        last_event_id: str | None = None,
    ) -> tuple[str, AsyncGenerator[SSEMessage, None]]:
        """Open a stream for a client and return its ID and event generator."""
        async with self._lock:
            state, replay_messages = self._get_or_create_stream(client_id, last_event_id)

        async def stream() -> AsyncGenerator[SSEMessage, None]:
            try:
                for message in replay_messages:
                    yield message
                while True:
                    yield await state.queue.get()
            finally:
                async with self._lock:
                    if state.stream_id in self._streams:
                        self._streams[state.stream_id].active = False

        return state.stream_id, stream()

    async def enqueue_message(self, client_id: str, message: dict[str, Any]) -> None:
        """Enqueue a message for a specific client."""
        await self.publish(message, client_id=client_id)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        await self.publish(message)

    async def subscribe(self, client_id: str) -> AsyncGenerator[SSEMessage, None]:
        """Backwards-compatible subscription helper."""
        _, stream = await self.open_stream(client_id)
        return stream

    def disconnect(self, stream_id: str) -> None:
        """Explicitly remove a stream and its buffered state."""
        state = self._streams.pop(stream_id, None)
        if state is None:
            return

        group = self._client_groups.get(state.client_id)
        if group is not None:
            group.stream_ids = [candidate for candidate in group.stream_ids if candidate != stream_id]
            if not group.stream_ids:
                self._client_groups.pop(state.client_id, None)

    async def publish(self, message: dict[str, Any], client_id: str | None = None) -> None:
        """Publish a JSON payload to one stream per client."""
        payload = encode_json(message).decode("utf-8")
        async with self._lock:
            target_client_ids = [client_id] if client_id is not None else list(self._client_groups.keys())
            for current_client_id in target_client_ids:
                group = self._client_groups.get(current_client_id)
                if group is None or not group.stream_ids:
                    continue
                stream_state = self._pick_stream_for_group(group)
                message_id = f"{stream_state.stream_id}:{len(stream_state.history)}"
                sse_message = SSEMessage(data=payload, id=message_id)
                stream_state.history.append(sse_message)
                await stream_state.queue.put(sse_message)

    def _get_or_create_stream(
        self,
        client_id: str,
        last_event_id: str | None,
    ) -> tuple[_StreamState, list[SSEMessage]]:
        if last_event_id:
            stream_id, event_index = self._parse_event_id(last_event_id)
            existing = self._streams.get(stream_id)
            if existing is not None and existing.client_id == client_id:
                existing.active = True
                replay_messages = existing.history[event_index + 1 :]
                return existing, replay_messages

        stream_id = str(uuid4())
        state = _StreamState(stream_id=stream_id, client_id=client_id)
        prime_message = SSEMessage(data="", id=f"{stream_id}:0")
        state.history.append(prime_message)
        state.queue.put_nowait(prime_message)
        self._streams[stream_id] = state
        self._client_groups.setdefault(client_id, _ClientGroup()).stream_ids.append(stream_id)
        return state, []

    def _pick_stream_for_group(self, group: _ClientGroup) -> _StreamState:
        active_stream_ids = [stream_id for stream_id in group.stream_ids if self._streams[stream_id].active]
        candidate_stream_ids = active_stream_ids or group.stream_ids
        selected_index = group.next_index % len(candidate_stream_ids)
        group.next_index += 1
        return self._streams[candidate_stream_ids[selected_index]]

    def _parse_event_id(self, value: str) -> tuple[str, int]:
        stream_id, _, raw_index = value.rpartition(":")
        if not stream_id:
            msg = "Invalid Last-Event-ID header"
            raise ValueError(msg)
        return stream_id, int(raw_index)
