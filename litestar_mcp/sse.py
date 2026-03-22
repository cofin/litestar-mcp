"""SSE transport management for MCP."""

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any


@dataclass
class SSEMessage:
    """Represents a single SSE message."""

    data: str
    event: str = "message"


class SSEManager:
    """Manages SSE connections and message delivery for MCP clients."""

    def __init__(self) -> None:
        """Initialize the SSE manager."""
        self._queues: dict[str, asyncio.Queue[SSEMessage]] = {}

    def _get_queue(self, client_id: str) -> asyncio.Queue[SSEMessage]:
        """Get or create a message queue for a client."""
        if client_id not in self._queues:
            self._queues[client_id] = asyncio.Queue()
        return self._queues[client_id]

    def register_client(self, client_id: str) -> None:
        """Register a client and create its message queue.

        Args:
            client_id: Unique identifier for the client.
        """
        self._get_queue(client_id)

    async def enqueue_message(self, client_id: str, message: dict[str, Any]) -> None:
        """Enqueue a message for a specific client.

        Args:
            client_id: Unique identifier for the client.
            message: The message dictionary to send.
        """
        queue = self._get_queue(client_id)

        # MCP SSE transport uses data-only SSE messages where the data is a JSON object
        # containing the event/id/etc.
        await queue.put(SSEMessage(data=json.dumps(message)))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients.

        Args:
            message: The message dictionary to broadcast.
        """
        if not self._queues:
            return

        sse_msg = SSEMessage(data=json.dumps(message))
        await asyncio.gather(*(queue.put(sse_msg) for queue in self._queues.values()))

    async def subscribe(self, client_id: str) -> AsyncGenerator[SSEMessage, None]:
        """Subscribe to a stream of messages for a client.

        Args:
            client_id: Unique identifier for the client.

        Yields:
            SSEMessage objects.
        """
        queue = self._get_queue(client_id)
        try:
            while True:
                yield await queue.get()
                queue.task_done()
        finally:
            # Cleanup queue on disconnect if desired
            pass

    def disconnect(self, client_id: str) -> None:
        """Explicitly cleanup a client's queue."""
        self._queues.pop(client_id, None)
