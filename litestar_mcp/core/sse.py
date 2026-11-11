"""In-process notification subscriptions for MCP Streamable HTTP."""

import asyncio
import contextlib
import inspect
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from litestar_mcp.core.serialization import from_json

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

__all__ = (
    "LOG_LEVEL_SEVERITY",
    "MCP_LOG_LEVELS",
    "MCPLogLevel",
    "StreamLimitExceeded",
    "SubscriptionManager",
    "log_level_allows",
    "validate_log_level",
)

MCPLogLevel = Literal["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"]
MCP_LOG_LEVELS: tuple[MCPLogLevel, ...] = (
    "debug",
    "info",
    "notice",
    "warning",
    "error",
    "critical",
    "alert",
    "emergency",
)
LOG_LEVEL_SEVERITY: dict[str, int] = {level: index for index, level in enumerate(MCP_LOG_LEVELS)}

_CLOSED = object()
_BROKER_CHANNEL = "litestar-mcp-subscriptions"
_BROKER_RETRY_INITIAL = 0.5
_BROKER_RETRY_MAX = 30.0
_logger = logging.getLogger(__name__)
_METHOD_FILTERS = {
    "notifications/tools/list_changed": "toolsListChanged",
    "notifications/prompts/list_changed": "promptsListChanged",
    "notifications/resources/list_changed": "resourcesListChanged",
    "notifications/skills/list_changed": "skillsListChanged",
}


def validate_log_level(level: "str") -> "MCPLogLevel":
    """Validate and return an RFC 5424 MCP log severity level."""
    if not isinstance(level, str) or level not in LOG_LEVEL_SEVERITY:
        msg = f"Invalid MCP log level: {level!r}"
        raise ValueError(msg)
    return cast("MCPLogLevel", level)


def log_level_allows(message_level: "str", threshold: "str") -> "bool":
    """Return whether `message_level` meets or exceeds the severity `threshold`."""
    validated_message = validate_log_level(message_level)
    validated_threshold = validate_log_level(threshold)
    return LOG_LEVEL_SEVERITY[validated_message] >= LOG_LEVEL_SEVERITY[validated_threshold]


class StreamLimitExceeded(Exception):  # noqa: N818
    """Raised when the configured subscription stream cap is reached."""


@dataclass
class _Subscription:
    stream_id: "str"
    subscription_id: "Any"
    notifications: "dict[str, Any]"
    queue: "asyncio.Queue[dict[str, Any] | object]"
    closed: "bool" = False
    session_id: "str | None" = None
    owner_id: "str | None" = None
    include_subscription_meta: "bool" = True
    send_acknowledgement: "bool" = True
    is_session_stream: "bool" = False
    authorize: "Callable[[str, dict[str, Any]], Awaitable[bool] | bool] | None" = None


class SubscriptionManager:
    """Manage filtered SSE notification streams across MCP protocol modes."""

    def __init__(
        self,
        *,
        max_streams: "int" = 10_000,
        queue_capacity: "int" = 256,
        channels: "Any | None" = None,
        authorizer: "Callable[[str | None, str, dict[str, Any]], Awaitable[bool] | bool] | None" = None,
        default_log_level: "str" = "info",
    ) -> "None":
        self._max_streams = max_streams
        self._queue_capacity = queue_capacity
        self._channels = channels
        self._authorizer = authorizer
        self._default_log_level: MCPLogLevel = validate_log_level(default_log_level)
        self._session_log_levels: dict[str, MCPLogLevel] = {}
        self._streams: dict[str, _Subscription] = {}
        self._lock = asyncio.Lock()
        self._broker_task: asyncio.Task[None] | None = None

    @property
    def channels(self) -> "Any | None":
        """Return the configured `ChannelsPlugin` broker instance, if any."""
        return self._channels

    def set_channels(self, channels: "Any | None") -> "None":
        """Configure or clear the `ChannelsPlugin` broker used for cross-worker fan-out."""
        self._channels = channels

    def set_default_log_level(self, level: "str") -> "MCPLogLevel":
        """Validate and update the manager's fallback RFC 5424 log severity threshold."""
        validated = validate_log_level(level)
        self._default_log_level = validated
        return validated

    def set_session_log_level(self, session_id: "str", level: "str") -> "MCPLogLevel":
        """Validate and record the active RFC 5424 log severity threshold for `session_id`."""
        validated = validate_log_level(level)
        if session_id:
            self._session_log_levels[session_id] = validated
        return validated

    def get_session_log_level(
        self,
        session_id: "str | None",
        *,
        default: "str | None" = None,
    ) -> "MCPLogLevel":
        """Return the active log severity threshold for `session_id`, or the fallback level."""
        fallback = validate_log_level(default) if default is not None else self._default_log_level
        if session_id is None:
            return fallback
        return self._session_log_levels.get(session_id, fallback)

    def clear_session_log_level(self, session_id: "str") -> "None":
        """Remove any cached log severity threshold bound to `session_id`."""
        self._session_log_levels.pop(session_id, None)

    def start(self) -> None:
        """Start cross-worker fan-out when a ChannelsPlugin was supplied."""
        if self._channels is not None and self._broker_task is None:
            self._broker_task = asyncio.create_task(self._consume_broker())

    async def open(
        self,
        subscription_id: "Any",
        notifications: "dict[str, Any]",
        *,
        owner_id: "str | None" = None,
        authorize: "Callable[[str, dict[str, Any]], Awaitable[bool] | bool] | None" = None,
    ) -> "tuple[str, AsyncGenerator[dict[str, Any], None]]":
        """Open a filtered stream whose first item is its acknowledgement."""
        async with self._lock:
            if len(self._streams) >= self._max_streams:
                msg = f"Subscription stream limit exceeded (max_streams={self._max_streams})"
                raise StreamLimitExceeded(msg)
            stream_id = str(uuid4())
            accepted = self._normalize_filter(notifications)
            send_acknowledgement = True
            state = _Subscription(
                stream_id=stream_id,
                subscription_id=subscription_id,
                notifications=accepted,
                queue=asyncio.Queue(maxsize=self._queue_capacity),
                owner_id=owner_id,
                include_subscription_meta=True,
                send_acknowledgement=send_acknowledgement,
                is_session_stream=not send_acknowledgement,
                authorize=authorize,
            )
            self._streams[stream_id] = state
            state.queue.put_nowait(self._acknowledgement(state))

        return stream_id, self._stream_events(stream_id, state)

    async def open_session_stream(
        self,
        *,
        session_id: "str | None" = None,
        owner_id: "str | None" = None,
        notifications: "dict[str, Any] | None" = None,
        authorize: "Callable[[str, dict[str, Any]], Awaitable[bool] | bool] | None" = None,
    ) -> "tuple[str, AsyncGenerator[dict[str, Any], None]]":
        """Open a server-initiated session SSE stream without an acknowledgement frame."""
        async with self._lock:
            if len(self._streams) >= self._max_streams:
                msg = f"Subscription stream limit exceeded (max_streams={self._max_streams})"
                raise StreamLimitExceeded(msg)
            stream_id = str(uuid4())
            base: dict[str, Any] = dict.fromkeys(_METHOD_FILTERS.values(), True)
            merged = {**base, **notifications} if notifications is not None else base
            accepted = self._normalize_filter(merged)
            send_acknowledgement = False
            state = _Subscription(
                stream_id=stream_id,
                subscription_id=None,
                notifications=accepted,
                queue=asyncio.Queue(maxsize=self._queue_capacity),
                session_id=session_id,
                owner_id=owner_id,
                include_subscription_meta=False,
                send_acknowledgement=send_acknowledgement,
                is_session_stream=not send_acknowledgement,
                authorize=authorize,
            )
            self._streams[stream_id] = state

        return stream_id, self._stream_events(stream_id, state)

    async def add_resource_subscription(self, session_id: "str", uri: "str") -> "None":
        """Subscribe all active streams bound to `session_id` to updates for `uri`."""
        async with self._lock:
            if not session_id or not uri:
                return
            for state in self._streams.values():
                if state.session_id != session_id:
                    continue
                existing = state.notifications.get("resourceSubscriptions", [])
                uris = list(existing) if isinstance(existing, list) else []
                state.notifications["resourceSubscriptions"] = list(dict.fromkeys([*uris, uri]))

    async def remove_resource_subscription(self, session_id: "str", uri: "str") -> "None":
        """Unsubscribe all active streams bound to `session_id` from updates for `uri`."""
        async with self._lock:
            if not session_id or not uri:
                return
            for state in self._streams.values():
                if state.session_id != session_id:
                    continue
                existing = state.notifications.get("resourceSubscriptions")
                if not isinstance(existing, list):
                    continue
                remaining = list(dict.fromkeys(item for item in existing if item != uri))
                if remaining:
                    state.notifications["resourceSubscriptions"] = remaining
                else:
                    state.notifications.pop("resourceSubscriptions", None)

    async def publish(
        self,
        method: "str",
        params: "dict[str, Any]",
        *,
        owner_id: "str | None" = None,
        session_id: "str | None" = None,
    ) -> "None":
        """Publish a notification only to authorized subscriptions whose filter, session, and owner match."""
        if self._channels is not None:
            envelope: dict[str, Any] = {"method": method, "params": params}
            if owner_id is not None:
                envelope["owner_id"] = owner_id
            if session_id is not None:
                envelope["session_id"] = session_id
            if hasattr(self._channels, "wait_published"):
                await self._channels.wait_published(envelope, channels=_BROKER_CHANNEL)
            else:
                outcome = self._channels.publish(envelope, channels=_BROKER_CHANNEL)
                if inspect.isawaitable(outcome):
                    await outcome
            return
        await self._publish_local(method, params, target_owner_id=owner_id, session_id=session_id)

    async def disconnect(self, stream_id: "str") -> "None":
        """Remove one stream and wake its consumer."""
        async with self._lock:
            state = self._streams.pop(stream_id, None)
        if state is not None:
            self._close(state)

    async def close_session(self, session_id: "str") -> "None":
        """Close and remove all active SSE streams and cached log thresholds bound to `session_id`."""
        async with self._lock:
            self._session_log_levels.pop(session_id, None)
            matching_ids = [stream_id for stream_id, state in self._streams.items() if state.session_id == session_id]
            states = [self._streams.pop(stream_id) for stream_id in matching_ids]
        for state in states:
            self._close(state)

    async def close_all(self) -> "None":
        """Gracefully close every active stream."""
        if self._broker_task is not None:
            self._broker_task.cancel()
            await asyncio.gather(self._broker_task, return_exceptions=True)
            self._broker_task = None
        async with self._lock:
            states = tuple(self._streams.values())
            self._streams.clear()
            self._session_log_levels.clear()
        for state in states:
            if state.send_acknowledgement:
                self._complete(state)
            else:
                self._close(state)

    async def _stream_events(
        self,
        stream_id: "str",
        state: "_Subscription",
    ) -> "AsyncGenerator[dict[str, Any], None]":
        try:
            while True:
                if state.closed and state.queue.empty():
                    return
                message = await state.queue.get()
                if message is _CLOSED or not isinstance(message, dict):
                    return
                yield message
        finally:
            await self.disconnect(stream_id)

    async def _publish_local(
        self,
        method: "str",
        params: "dict[str, Any]",
        *,
        target_owner_id: "str | None" = None,
        owner_id: "str | None" = None,
        session_id: "str | None" = None,
    ) -> "None":
        resolved_owner_id = target_owner_id if target_owner_id is not None else owner_id
        async with self._lock:
            states = tuple(self._streams.values())
        for state in states:
            if state.closed:
                continue
            if resolved_owner_id is not None and state.owner_id != resolved_owner_id:
                continue
            if not self._matches_stream(state, method, params, target_session_id=session_id):
                continue
            if not await self._is_authorized(state, method, params):
                continue
            if state.closed or not self._matches_stream(state, method, params, target_session_id=session_id):
                continue
            tagged_params = self._format_params(state, params)
            try:
                state.queue.put_nowait({"jsonrpc": "2.0", "method": method, "params": tagged_params})
            except asyncio.QueueFull:
                async with self._lock:
                    self._streams.pop(state.stream_id, None)
                _logger.warning("Closing slow MCP subscriber %s after queue overflow", state.stream_id)
                if state.send_acknowledgement:
                    self._complete(state)
                else:
                    self._close(state)

    def _matches_stream(
        self,
        state: "_Subscription",
        method: "str",
        params: "dict[str, Any]",
        *,
        target_session_id: "str | None" = None,
    ) -> "bool":
        if method == "notifications/progress":
            return bool(
                state.is_session_stream and target_session_id is not None and state.session_id == target_session_id
            )
        if method == "notifications/message":
            return self._matches_log_stream(state, params, target_session_id=target_session_id)
        if target_session_id is not None and state.session_id != target_session_id:
            return False
        return self._matches(state.notifications, method, params)

    def _matches_log_stream(
        self,
        state: "_Subscription",
        params: "dict[str, Any]",
        *,
        target_session_id: "str | None" = None,
    ) -> "bool":
        msg_level = params.get("level")
        if not isinstance(msg_level, str) or msg_level not in LOG_LEVEL_SEVERITY:
            return False
        if state.is_session_stream:
            if target_session_id is not None and state.session_id != target_session_id:
                return False
            return log_level_allows(msg_level, self.get_session_log_level(state.session_id))
        if target_session_id is not None:
            return False
        threshold = state.notifications.get("logLevel")
        if not isinstance(threshold, str) or threshold not in LOG_LEVEL_SEVERITY:
            return False
        return log_level_allows(msg_level, threshold)

    async def _is_authorized(self, state: "_Subscription", method: "str", params: "dict[str, Any]") -> "bool":
        if state.authorize is not None:
            try:
                outcome = state.authorize(method, dict(params))
                allowed = bool(await outcome) if inspect.isawaitable(outcome) else bool(outcome)
            except Exception:  # noqa: BLE001
                _logger.warning("MCP stream authorization predicate failed for %s; denying delivery", state.stream_id)
                return False
            if not allowed:
                return False
        if self._authorizer is not None:
            try:
                outcome = self._authorizer(state.owner_id, method, dict(params))
                allowed = bool(await outcome) if inspect.isawaitable(outcome) else bool(outcome)
            except Exception:  # noqa: BLE001
                _logger.warning("MCP subscription manager authorizer failed for %s; denying delivery", state.stream_id)
                return False
            if not allowed:
                return False
        return True

    @staticmethod
    def _format_params(state: "_Subscription", params: "dict[str, Any]") -> "dict[str, Any]":
        tagged_params = dict(params)
        if state.include_subscription_meta:
            meta = dict(tagged_params.get("_meta") or {})
            meta["io.modelcontextprotocol/subscriptionId"] = state.subscription_id
            tagged_params["_meta"] = meta
            return tagged_params
        raw_meta = tagged_params.get("_meta")
        if isinstance(raw_meta, dict):
            cleaned_meta = {
                key: value for key, value in raw_meta.items() if key != "io.modelcontextprotocol/subscriptionId"
            }
            if cleaned_meta:
                tagged_params["_meta"] = cleaned_meta
            else:
                tagged_params.pop("_meta", None)
        return tagged_params

    @classmethod
    def _complete(cls, state: "_Subscription") -> "None":
        """End a stream with its terminal response, dropping undelivered notifications if the queue is full."""
        state.closed = True
        try:
            state.queue.put_nowait(cls._completion(state))
        except asyncio.QueueFull:
            while not state.queue.empty():
                state.queue.get_nowait()
            state.queue.put_nowait(cls._completion(state))
        cls._close(state)

    @staticmethod
    def _close(state: "_Subscription") -> "None":
        state.closed = True
        with contextlib.suppress(asyncio.QueueFull):
            state.queue.put_nowait(_CLOSED)

    async def _consume_broker(self) -> "None":
        channels = self._channels
        if channels is None:
            return
        delay = _BROKER_RETRY_INITIAL
        while True:
            try:
                async with channels.start_subscription(_BROKER_CHANNEL) as subscriber:
                    delay = _BROKER_RETRY_INITIAL
                    async for event in subscriber.iter_events():
                        await self._publish_broker_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("MCP subscription broker failed; resubscribing in %.1fs", delay)
            else:
                _logger.warning("MCP subscription broker stream ended; resubscribing in %.1fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _BROKER_RETRY_MAX)

    async def _publish_broker_event(self, event: "Any") -> "None":
        try:
            payload = from_json(event)
        except Exception:  # noqa: BLE001
            _logger.warning("Ignoring undecodable MCP subscription broker event")
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("method"), str):
            return
        params = payload.get("params")
        if not isinstance(params, dict):
            return
        raw_owner = payload.get("owner_id")
        if raw_owner is not None and not isinstance(raw_owner, str):
            _logger.warning("Ignoring MCP subscription broker event with invalid owner_id")
            return
        raw_session = payload.get("session_id")
        if raw_session is not None and not isinstance(raw_session, str):
            _logger.warning("Ignoring MCP subscription broker event with invalid session_id")
            return
        await self._publish_local(
            payload["method"],
            params,
            target_owner_id=raw_owner,
            session_id=raw_session,
        )

    def _normalize_filter(self, notifications: "dict[str, Any]") -> "dict[str, Any]":
        accepted: dict[str, Any] = {}
        for filter_name in tuple(_METHOD_FILTERS.values()):
            if notifications.get(filter_name) is True:
                accepted[filter_name] = True
        resources = notifications.get("resourceSubscriptions")
        if isinstance(resources, list) and all(isinstance(uri, str) for uri in resources):
            accepted["resourceSubscriptions"] = list(dict.fromkeys(resources))
        tasks = notifications.get("taskIds")
        if isinstance(tasks, list) and all(isinstance(task_id, str) for task_id in tasks):
            accepted["taskIds"] = list(dict.fromkeys(tasks))
        if "logging" in notifications:
            resolved_logging = self._resolve_logging_filter(notifications["logging"], self._default_log_level)
            if resolved_logging is not None:
                accepted["logLevel"] = resolved_logging
        if "logLevel" in notifications:
            accepted["logLevel"] = validate_log_level(notifications["logLevel"])
        return accepted

    @staticmethod
    def _resolve_logging_filter(raw_logging: "Any", default_level: "MCPLogLevel") -> "MCPLogLevel | None":
        if raw_logging is False:
            return None
        if raw_logging is True:
            return default_level
        if isinstance(raw_logging, dict):
            if not raw_logging:
                return default_level
            if "level" in raw_logging:
                return validate_log_level(raw_logging["level"])
            msg = f"Invalid logging subscription filter: {raw_logging!r}"
            raise ValueError(msg)
        msg = f"Invalid logging subscription filter: {raw_logging!r}"
        raise ValueError(msg)

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
    def _completion(state: "_Subscription") -> "dict[str, Any]":
        return {
            "jsonrpc": "2.0",
            "id": state.subscription_id,
            "result": {
                "resultType": "complete",
                "_meta": {"io.modelcontextprotocol/subscriptionId": state.subscription_id},
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
