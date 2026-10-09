"""Unit tests for MemorySessionStore and LitestarStoreSessionStore implementations."""

import time
from typing import Any

import pytest
from litestar.stores.memory import MemoryStore

from litestar_mcp.agent.sessions import (
    ANONYMOUS_OWNER,
    LitestarStoreSessionStore,
    MemorySessionStore,
    SessionStore,
)
from litestar_mcp.agent.spec import AgentMessage


@pytest.fixture(params=["memory", "litestar_store"])
def session_store(request: Any) -> SessionStore:
    """Fixture providing parameterized SessionStore instances."""
    if request.param == "memory":
        return MemorySessionStore(ttl_seconds=3600.0)
    return LitestarStoreSessionStore(MemoryStore(), ttl_seconds=3600)


@pytest.mark.anyio
async def test_session_store_round_trip(session_store: SessionStore) -> None:
    """SessionStore saves and loads conversation messages accurately."""
    messages = [
        AgentMessage(role="user", content="hello"),
        AgentMessage(role="assistant", content="world", agent_name="bot"),
    ]
    await session_store.save("tenant1:user1", "sess-1", messages)
    loaded = await session_store.load("tenant1:user1", "sess-1")
    assert loaded == messages

    missing = await session_store.load("tenant1:user1", "nonexistent")
    assert missing is None


@pytest.mark.anyio
async def test_session_store_owner_isolation(session_store: SessionStore) -> None:
    """SessionStore isolates sessions between different owners and anonymous users."""
    messages = [AgentMessage(role="user", content="secret")]
    await session_store.save("user-a", "shared-id", messages)

    loaded_a = await session_store.load("user-a", "shared-id")
    loaded_b = await session_store.load("user-b", "shared-id")
    loaded_anon = await session_store.load(ANONYMOUS_OWNER, "shared-id")

    assert loaded_a == messages
    assert loaded_b is None
    assert loaded_anon is None


@pytest.mark.anyio
async def test_session_store_lock_is_per_session(session_store: SessionStore) -> None:
    """SessionStore locks are independent across different session IDs."""
    async with session_store.lock("user-1", "sess-a"), session_store.lock("user-1", "sess-b"):
        pass


@pytest.mark.anyio
async def test_memory_session_store_lru_eviction() -> None:
    """MemorySessionStore evicts oldest accessed session when capacity is reached."""
    store = MemorySessionStore(max_sessions=2)
    m1 = [AgentMessage(role="user", content="first")]
    m2 = [AgentMessage(role="user", content="second")]
    m3 = [AgentMessage(role="user", content="third")]

    await store.save("o", "s1", m1)
    await store.save("o", "s2", m2)

    await store.load("o", "s1")

    await store.save("o", "s3", m3)

    assert await store.load("o", "s2") is None
    assert await store.load("o", "s1") == m1
    assert await store.load("o", "s3") == m3


@pytest.mark.anyio
async def test_memory_session_store_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """MemorySessionStore expires sessions when current time exceeds TTL."""
    current_time = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: current_time)

    store = MemorySessionStore(ttl_seconds=30.0)
    messages = [AgentMessage(role="user", content="expiring")]
    await store.save("o", "s1", messages)

    assert await store.load("o", "s1") == messages

    current_time += 31.0
    assert await store.load("o", "s1") is None
