"""Session store implementations for agent conversation history persistence."""

import time
from collections import OrderedDict
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Protocol

import anyio
from litestar.serialization import decode_json, encode_json

from litestar_mcp.agent.spec import AgentMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from litestar.stores.base import Store

__all__ = (
    "ANONYMOUS_OWNER",
    "LitestarStoreSessionStore",
    "MemorySessionStore",
    "SessionStore",
)

ANONYMOUS_OWNER = "anonymous"


class SessionStore(Protocol):
    """Persist conversation history keyed by owner and session id."""

    async def load(self, owner: "str", session_id: "str") -> "list[AgentMessage] | None":
        """Load messages for an owner and session id, returning None if nonexistent."""
        ...

    async def save(self, owner: "str", session_id: "str", messages: "Sequence[AgentMessage]") -> "None":
        """Persist messages for an owner and session id."""
        ...

    def lock(self, owner: "str", session_id: "str") -> "AbstractAsyncContextManager[None]":
        """Acquire a per-session lock context manager."""
        ...


class MemorySessionStore:
    """Process-local LRU session store with TTL expiry and per-session locks."""

    def __init__(self, *, max_sessions: "int" = 1024, ttl_seconds: "float" = 3600.0) -> "None":
        """Initialize in-memory session store."""
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._sessions: OrderedDict[tuple[str, str], tuple[float, list[AgentMessage]]] = OrderedDict()
        self._locks: dict[tuple[str, str], anyio.Lock] = {}

    def _get_lock(self, key: "tuple[str, str]") -> "anyio.Lock":
        """Retrieve or create lock for key."""
        lock = self._locks.get(key)
        if lock is None:
            lock = anyio.Lock()
            self._locks[key] = lock
        return lock

    @asynccontextmanager
    async def lock(self, owner: "str", session_id: "str") -> "AsyncIterator[None]":
        """Acquire per-session lock."""
        key = (owner, session_id)
        lock = self._get_lock(key)
        async with lock:
            yield

    async def load(self, owner: "str", session_id: "str") -> "list[AgentMessage] | None":
        """Load messages if entry exists and has not expired."""
        key = (owner, session_id)
        entry = self._sessions.get(key)
        if entry is None:
            return None
        saved_at, messages = entry
        if time.monotonic() - saved_at > self._ttl_seconds:
            self._sessions.pop(key, None)
            return None
        self._sessions.move_to_end(key)
        return list(messages)

    async def save(self, owner: "str", session_id: "str", messages: "Sequence[AgentMessage]") -> "None":
        """Store messages and evict oldest when capacity exceeded."""
        key = (owner, session_id)
        self._sessions[key] = (time.monotonic(), list(messages))
        self._sessions.move_to_end(key)
        while len(self._sessions) > self._max_sessions:
            evicted_key, _ = self._sessions.popitem(last=False)
            self._locks.pop(evicted_key, None)


class LitestarStoreSessionStore:
    """Session store over a Litestar Store backend."""

    def __init__(
        self,
        store: "Store",
        *,
        ttl_seconds: "int" = 3600,
        key_prefix: "str" = "litestar_mcp:session",
    ) -> "None":
        """Initialize store backed by Litestar Store."""
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix
        self._locks: dict[str, anyio.Lock] = {}

    def _format_key(self, owner: "str", session_id: "str") -> "str":
        """Format full storage key."""
        return f"{self._key_prefix}:{owner}:{session_id}"

    def _get_lock(self, key: "str") -> "anyio.Lock":
        """Retrieve or create lock for formatted key."""
        lock = self._locks.get(key)
        if lock is None:
            lock = anyio.Lock()
            self._locks[key] = lock
        return lock

    @asynccontextmanager
    async def lock(self, owner: "str", session_id: "str") -> "AsyncIterator[None]":
        """Acquire per-session lock."""
        key = self._format_key(owner, session_id)
        lock = self._get_lock(key)
        async with lock:
            yield

    async def load(self, owner: "str", session_id: "str") -> "list[AgentMessage] | None":
        """Load and decode conversation messages from storage backend."""
        key = self._format_key(owner, session_id)
        raw = await self._store.get(key)
        if raw is None:
            return None
        return decode_json(raw, target_type=list[AgentMessage])

    async def save(self, owner: "str", session_id: "str", messages: "Sequence[AgentMessage]") -> "None":
        """Encode and persist conversation messages to storage backend."""
        key = self._format_key(owner, session_id)
        data = encode_json(list(messages))
        await self._store.set(key, data, expires_in=self._ttl_seconds)
