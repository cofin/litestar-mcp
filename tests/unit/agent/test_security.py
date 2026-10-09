from __future__ import annotations

from litestar_mcp.agent.security import ToolContext, resolve_tool_context


def test_resolve_tool_context_empty() -> None:
    ctx = resolve_tool_context(None)
    assert isinstance(ctx, ToolContext)
    assert ctx.user is None
    assert ctx.tenant_id is None
    assert ctx.roles == []
    assert ctx.scopes == []


def test_resolve_tool_context_with_user_and_scope() -> None:
    class MockUser:
        def __init__(self) -> None:
            self.roles = ["admin", "editor"]
            self.scopes = ["read", "write"]
            self.tenant_id = "tenant-abc"

    class MockConnection:
        def __init__(self) -> None:
            user = MockUser()
            self.scope = {
                "user": user,
                "session": {"session_id": "sess-123"},
            }
            self.user = user

    conn = MockConnection()
    ctx = resolve_tool_context(conn)
    assert ctx.user is not None
    assert ctx.roles == ["admin", "editor"]
    assert ctx.scopes == ["read", "write"]
    assert ctx.tenant_id == "tenant-abc"
    assert ctx.session_id == "sess-123"


def test_resolve_tool_context_with_auth_and_state() -> None:
    class MockAuth:
        tenant_id = "tenant-from-auth"

    class MockState:
        tenant = "tenant-from-state"

    class MockConnection:
        def __init__(self) -> None:
            self.scope = {
                "auth": MockAuth(),
            }
            self.state = MockState()

    conn = MockConnection()
    ctx = resolve_tool_context(conn)
    assert ctx.user is not None
    assert ctx.tenant_id == "tenant-from-auth"
