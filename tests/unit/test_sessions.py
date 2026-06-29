"""Tests for the MCP session manager (Store-backed)."""

from typing import Any

import pytest
from litestar.stores.memory import MemoryStore

from litestar_mcp.sessions import MCPSession, MCPSessionManager, SessionTerminated


@pytest.fixture
def store() -> "MemoryStore":
    return MemoryStore()


@pytest.fixture
def manager(store: "MemoryStore") -> "MCPSessionManager":
    return MCPSessionManager(store, max_idle_seconds=60.0)


@pytest.mark.asyncio
async def test_create_returns_session_with_id(manager: "MCPSessionManager", store: "MemoryStore") -> "None":
    session = await manager.create(protocol_version="2025-11-25", client_info={"name": "test"})
    assert session.id
    assert session.protocol_version == "2025-11-25"
    assert session.client_info == {"name": "test"}
    assert session.initialized is False
    raw = await store.get(session.id)
    assert raw is not None


@pytest.mark.asyncio
async def test_get_returns_same_session(manager: "MCPSessionManager") -> "None":
    created = await manager.create(protocol_version="2025-11-25")
    fetched = await manager.get(created.id)
    assert fetched.id == created.id
    assert fetched.protocol_version == created.protocol_version


@pytest.mark.asyncio
async def test_get_unknown_raises(manager: "MCPSessionManager") -> "None":
    with pytest.raises(SessionTerminated):
        await manager.get("no-such-id")


@pytest.mark.asyncio
async def test_get_after_expiry_raises(store: "MemoryStore") -> "None":
    manager = MCPSessionManager(store, max_idle_seconds=1.0)
    session = await manager.create(protocol_version="2025-11-25")
    # Force-expire by deleting the underlying entry
    await store.delete(session.id)
    with pytest.raises(SessionTerminated):
        await manager.get(session.id)


@pytest.mark.asyncio
async def test_mark_initialized(manager: "MCPSessionManager") -> "None":
    session = await manager.create(protocol_version="2025-11-25")
    assert session.initialized is False
    await manager.mark_initialized(session.id)
    refreshed = await manager.get(session.id)
    assert refreshed.initialized is True


@pytest.mark.asyncio
async def test_mark_initialized_unknown_raises(manager: "MCPSessionManager") -> "None":
    with pytest.raises(SessionTerminated):
        await manager.mark_initialized("nope")


@pytest.mark.asyncio
async def test_delete_removes_session(manager: "MCPSessionManager") -> "None":
    session = await manager.create(protocol_version="2025-11-25")
    await manager.delete(session.id)
    with pytest.raises(SessionTerminated):
        await manager.get(session.id)


@pytest.mark.asyncio
async def test_delete_is_idempotent(manager: "MCPSessionManager") -> "None":
    await manager.delete("no-such-id")  # must not raise


def test_mcp_session_struct_fields() -> "None":
    s = MCPSession(id="x", protocol_version="v", created_at=0.0, last_activity=0.0)
    assert s.capabilities == {}
    assert isinstance(s.client_info, dict)


@pytest.mark.asyncio
async def test_touch_renews_last_activity(manager: "MCPSessionManager") -> "None":
    session = await manager.create(protocol_version="2025-11-25")
    initial_activity = session.last_activity
    refreshed = await manager.touch(session.id)
    assert refreshed.last_activity >= initial_activity


@pytest.mark.asyncio
async def test_manager_uses_custom_capabilities_and_client_info(manager: "MCPSessionManager") -> "None":
    caps: dict[str, Any] = {"tools": {"listChanged": True}}
    info: dict[str, Any] = {"name": "claude-desktop", "version": "1.0"}
    session = await manager.create(protocol_version="2025-11-25", capabilities=caps, client_info=info)
    stored = await manager.get(session.id)
    assert stored.capabilities == caps
    assert stored.client_info == info


@pytest.mark.asyncio
async def test_validate_session_exempt_method_no_id(manager: "MCPSessionManager") -> "None":
    # Exempt method (ping) with no session ID should return None
    session = await manager.validate_session(None, "ping")
    assert session is None


@pytest.mark.asyncio
async def test_validate_session_exempt_method_with_valid_id(manager: "MCPSessionManager") -> "None":
    created = await manager.create(protocol_version="2025-11-25")
    # Exempt method with valid session ID should validate and return the session
    session = await manager.validate_session(created.id, "ping")
    assert session is not None
    assert session.id == created.id


@pytest.mark.asyncio
async def test_validate_session_exempt_method_with_invalid_id(manager: "MCPSessionManager") -> "None":
    # Exempt method with invalid session ID should raise SessionTerminated
    with pytest.raises(SessionTerminated):
        await manager.validate_session("invalid-id", "ping")


@pytest.mark.asyncio
async def test_validate_session_missing_id_raises(manager: "MCPSessionManager") -> "None":
    from litestar_mcp.sessions import SessionMissingError

    # Non-exempt method (tools/list) with no session ID should raise SessionMissingError
    with pytest.raises(SessionMissingError):
        await manager.validate_session(None, "tools/list")


@pytest.mark.asyncio
async def test_validate_session_invalid_id_raises(manager: "MCPSessionManager") -> "None":
    from litestar_mcp.sessions import SessionMissingError  # noqa: F401

    # Non-exempt method with invalid session ID should raise SessionTerminated
    with pytest.raises(SessionTerminated):
        await manager.validate_session("invalid-id", "tools/list")


@pytest.mark.asyncio
async def test_validate_session_valid_initialized(manager: "MCPSessionManager") -> "None":
    created = await manager.create(protocol_version="2025-11-25")
    await manager.mark_initialized(created.id)
    # Non-exempt method with initialized session should succeed
    session = await manager.validate_session(created.id, "tools/list")
    assert session is not None
    assert session.id == created.id
    assert session.initialized is True


@pytest.mark.asyncio
async def test_validate_session_uninitialized_raises(manager: "MCPSessionManager") -> "None":
    from litestar_mcp.sessions import SessionNotInitializedError

    created = await manager.create(protocol_version="2025-11-25")
    # Non-exempt method (tools/list) with uninitialized session should raise SessionNotInitializedError
    with pytest.raises(SessionNotInitializedError):
        await manager.validate_session(created.id, "tools/list")


@pytest.mark.asyncio
async def test_validate_session_uninitialized_pre_init_allowed(manager: "MCPSessionManager") -> "None":
    created = await manager.create(protocol_version="2025-11-25")
    # Pre-init allowed method (notifications/initialized) with uninitialized session should succeed
    session = await manager.validate_session(created.id, "notifications/initialized")
    assert session is not None
    assert session.id == created.id
    assert session.initialized is False


@pytest.mark.asyncio
async def test_validate_session_touches_session(manager: "MCPSessionManager") -> "None":
    created = await manager.create(protocol_version="2025-11-25")
    await manager.mark_initialized(created.id)
    initial_activity = (await manager.get(created.id)).last_activity

    # Wait a small moment to ensure timestamp moves if clock precision is high enough
    import asyncio

    await asyncio.sleep(0.01)

    # Validate session should touch and update last_activity
    validated = await manager.validate_session(created.id, "tools/list")
    assert validated is not None
    assert validated.last_activity > initial_activity

    # Verify state in store is also updated
    stored = await manager.get(created.id)
    assert stored.last_activity > initial_activity
