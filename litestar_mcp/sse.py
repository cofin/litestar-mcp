"""In-process notification subscriptions for MCP 2026-07-28."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from litestar.serialization import decode_json

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

__all__ = ("StreamLimitExceeded", "SubscriptionManager")

_CLOSED = object()
_METHOD_FILTERS = {
    "notifications/tools/list_changed": "toolsListChanged",
    "notifications/prompts/list_changed": "promptsListChanged",
    "notifications/resources/list_changed": "resourcesListChanged",
}


class StreamLimitExceeded(Exception):  # noqa: N818
    """Raised when the configured subscription stream cap is reached."""


@dataclass
class _Subscription:
    stream_id: "str"
    subscription_id: "Any"
    notifications: "dict[str, Any]"
    queue: "asyncio.Queue[dict[str, Any] | object]" = field(default_factory=asyncio.Queue)


class SubscriptionManager:
    """Manage stateless, filtered subscription response streams."""

    def __init__(self, *, max_streams: "int" = 10_000, channels: "Any | None" = None) -> "None":
        self._max_streams = max_streams
        self._channels = channels
        self._streams: dict[str, _Subscription] = {}
        self._lock = asyncio.Lock()
        self._broker_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start cross-worker fan-out when a ChannelsPlugin was supplied."""
        if self._channels is not None and self._broker_task is None:
            self._broker_task = asyncio.create_task(self._consume_broker())

    async def open(
        self,
        subscription_id: "Any",
        notifications: "dict[str, Any]",
    ) -> "tuple[str, AsyncGenerator[dict[str, Any], None]]":
        """Open a filtered stream whose first item is its acknowledgement."""
        async with self._lock:
            if len(self._streams) >= self._max_streams:
                msg = f"Subscription stream limit exceeded (max_streams={self._max_streams})"
                raise StreamLimitExceeded(msg)
            stream_id = str(uuid4())
            accepted = self._normalize_filter(notifications)
            state = _Subscription(
                stream_id=stream_id,
                subscription_id=subscription_id,
                notifications=accepted,
            )
            self._streams[stream_id] = state
            state.queue.put_nowait(self._acknowledgement(state))

        async def stream() -> "AsyncGenerator[dict[str, Any], None]":
            try:
                while True:
                    message = await state.queue.get()
                    if message is _CLOSED:
                        return
                    yield message  # type: ignore[misc]
            finally:
                await self.disconnect(stream_id)

        return stream_id, stream()

    async def publish(self, method: "str", params: "dict[str, Any]") -> "None":
        """Publish a notification only to subscriptions whose filter matches."""
        if self._channels is not None:
            self._channels.publish(
                {"method": method, "params": params},
                channels="litestar-mcp-subscriptions",
            )
            return
        await self._publish_local(method, params)

    async def _publish_local(self, method: "str", params: "dict[str, Any]") -> "None":
        async with self._lock:
            states = tuple(self._streams.values())
        for state in states:
            if not self._matches(state.notifications, method, params):
                continue
            tagged_params = dict(params)
            meta = dict(tagged_params.get("_meta") or {})
            meta["io.modelcontextprotocol/subscriptionId"] = state.subscription_id
            tagged_params["_meta"] = meta
            state.queue.put_nowait({"jsonrpc": "2.0", "method": method, "params": tagged_params})

    async def disconnect(self, stream_id: "str") -> "None":
        """Remove one stream and wake its consumer."""
        async with self._lock:
            state = self._streams.pop(stream_id, None)
        if state is not None:
            state.queue.put_nowait(_CLOSED)

    async def close_all(self) -> "None":
        """Gracefully close every active stream."""
        if self._broker_task is not None:
            self._broker_task.cancel()
            await asyncio.gather(self._broker_task, return_exceptions=True)
            self._broker_task = None
        async with self._lock:
            states = tuple(self._streams.values())
            self._streams.clear()
        for state in states:
            state.queue.put_nowait(_CLOSED)

    async def _consume_broker(self) -> "None":
        channels = self._channels
        if channels is None:
            return
        async with channels.start_subscription("litestar-mcp-subscriptions") as subscriber:
            async for event in subscriber.iter_events():
                payload = decode_json(event)
                if isinstance(payload, dict) and isinstance(payload.get("method"), str):
                    params = payload.get("params")
                    if isinstance(params, dict):
                        await self._publish_local(payload["method"], params)

    @staticmethod
    def _normalize_filter(notifications: "dict[str, Any]") -> "dict[str, Any]":
        accepted: dict[str, Any] = {}
        for filter_name in ("toolsListChanged", "promptsListChanged", "resourcesListChanged"):
            if notifications.get(filter_name) is True:
                accepted[filter_name] = True
        resources = notifications.get("resourceSubscriptions")
        if isinstance(resources, list) and all(isinstance(uri, str) for uri in resources):
            accepted["resourceSubscriptions"] = list(dict.fromkeys(resources))
        tasks = notifications.get("taskIds")
        if isinstance(tasks, list) and all(isinstance(task_id, str) for task_id in tasks):
            accepted["taskIds"] = list(dict.fromkeys(tasks))
        return accepted

    @staticmethod
    def _acknowledgement(state: "_Subscription") -> "dict[str, Any]":
        return {
            "jsonrpc": "2.0",
            "method": "notifications/subscriptions/acknowledged",
            "params": {
                "_meta": {"io.modelcontextprotocol/subscriptionId": state.subscription_id},
                "notifications": state.notifications,
            },
        }

    @staticmethod
    def _matches(notifications: "dict[str, Any]", method: "str", params: "dict[str, Any]") -> "bool":
        filter_name = _METHOD_FILTERS.get(method)
        if filter_name is not None:
            return notifications.get(filter_name) is True
        if method == "notifications/resources/updated":
            return params.get("uri") in notifications.get("resourceSubscriptions", ())
        if method == "notifications/tasks":
            return params.get("taskId") in notifications.get("taskIds", ())
        return False
