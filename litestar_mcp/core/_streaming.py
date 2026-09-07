"""Private ownership for request producers consumed by native streaming responses."""

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

import anyio
from anyio.lowlevel import checkpoint, checkpoint_if_cancelled
from litestar.enums import ScopeType
from litestar.middleware import ASGIMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from anyio.streams.memory import MemoryObjectSendStream
    from litestar.types import ASGIApp, Receive, Scope, Send

DEFAULT_STREAM_CLEANUP_TIMEOUT = 5.0
_STREAM_STATE_KEY = "litestar_mcp.stream_state"
_T = TypeVar("_T")
_R = TypeVar("_R")
_logger = logging.getLogger(__name__)


def validate_stream_cleanup_timeout(timeout: "float") -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        msg = "stream_cleanup_timeout must be positive and finite"
        raise ValueError(msg)


class StreamOwner(Generic[_T, _R]):
    """Keep a bounded channel and its producer alive until request cleanup finishes."""

    def __init__(self, capacity: "int", cleanup_timeout: "float") -> None:
        self.sender, self.receiver = anyio.create_memory_object_stream[_T](capacity)
        self._cleanup_timeout = cleanup_timeout
        self._task: asyncio.Task[_R] | None = None
        self._deadline: float | None = None
        self._timed_out = False
        self._result_observed = False

    def start(self, producer: "Callable[[MemoryObjectSendStream[_T]], Awaitable[_R]]") -> None:
        self._task = asyncio.create_task(self._run(producer))

    async def _run(self, producer: "Callable[[MemoryObjectSendStream[_T]], Awaitable[_R]]") -> "_R":
        try:
            return await producer(self.sender)
        finally:
            self.sender.close()

    async def result(self) -> "_R":
        if self._task is None:
            msg = "Stream producer has not started"
            raise RuntimeError(msg)
        self._result_observed = True
        return await asyncio.shield(self._task)

    def _retrieve_exception(self, task: "asyncio.Task[_R]") -> None:
        if not task.cancelled() and (error := task.exception()) is not None and not self._result_observed:
            _logger.error("Stream producer failed during cleanup", exc_info=error)

    async def close(self) -> None:
        self.receiver.close()
        self.sender.close()
        task = self._task
        if task is None or task is asyncio.current_task():
            return
        if self._deadline is None:
            self._deadline = asyncio.get_running_loop().time() + self._cleanup_timeout
            if not task.done():
                task.cancel()
        cancelled = False
        with anyio.CancelScope(shield=True):
            while not task.done():
                remaining = self._deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    if not self._timed_out:
                        self._timed_out = True
                        _logger.warning("Stream producer cleanup incomplete after %s seconds", self._cleanup_timeout)
                        task.cancel()
                        task.add_done_callback(self._retrieve_exception)
                    break
                try:
                    await asyncio.wait({task}, timeout=remaining)
                except asyncio.CancelledError:
                    cancelled = True
            if task.done():
                self._retrieve_exception(task)
        if cancelled:
            raise asyncio.CancelledError


@dataclass
class _StreamState:
    cancel_scope: "anyio.CancelScope"
    owner: "StreamOwner[Any, Any] | None" = None


class StreamCleanupMiddleware(ASGIMiddleware):
    """Own request producers even when native SSE is interrupted during send."""

    scopes = (ScopeType.HTTP,)

    async def handle(self, scope: "Scope", receive: "Receive", send: "Send", next_app: "ASGIApp") -> None:
        with anyio.CancelScope() as cancel_scope:
            state = _StreamState(cancel_scope)
            scope_dict = cast("dict[str, Any]", scope)
            scope_dict[_STREAM_STATE_KEY] = state
            try:
                await next_app(scope, receive, send)
            finally:
                try:
                    if state.owner is not None:
                        await state.owner.close()
                finally:
                    scope_dict.pop(_STREAM_STATE_KEY, None)


def start_stream(
    scope: "Scope",
    producer: "Callable[[MemoryObjectSendStream[_T]], Awaitable[_R]]",
    *,
    capacity: "int",
    cleanup_timeout: "float",
) -> "StreamOwner[_T, _R]":
    state = cast("_StreamState", cast("dict[str, Any]", scope)[_STREAM_STATE_KEY])
    owner: StreamOwner[_T, _R] = StreamOwner(capacity, cleanup_timeout)
    state.owner = owner
    owner.start(producer)
    return owner


async def prefetch_stream(scope: "Scope", receive: "Receive", owner: "StreamOwner[_T, Any]") -> "_T":
    """Temporarily own receive until native SSE can take over disconnect detection."""
    state = cast("_StreamState", cast("dict[str, Any]", scope)[_STREAM_STATE_KEY])

    async def watch_disconnect() -> None:
        while True:
            try:
                message = await receive()
            except Exception:
                _logger.exception("ASGI receive failed during stream prefetch")
                state.cancel_scope.cancel()
                return
            if message["type"] == "http.request" and not message.get("body") and not message.get("more_body", False):
                await checkpoint()
                continue
            if message["type"] != "http.disconnect":
                _logger.warning("Unexpected ASGI message during stream prefetch; aborting request")
            state.cancel_scope.cancel()
            return

    watcher = asyncio.create_task(watch_disconnect())
    try:
        try:
            return await owner.receiver.receive()
        except anyio.EndOfStream:
            await owner.result()
            raise
    finally:
        watcher.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(watcher, return_exceptions=True)
        await checkpoint_if_cancelled()
