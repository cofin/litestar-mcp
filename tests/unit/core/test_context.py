"""Unit tests for ToolContext and context resolution."""

from unittest.mock import MagicMock

from litestar_mcp.core.context import ToolContext


def test_tool_context_defaults() -> None:
    """Verify default attribute values on a fresh ToolContext instance."""
    ctx = ToolContext()
    assert ctx.tenant_id is None
    assert ctx.user is None
    assert ctx.request is None
    assert ctx.session_id is None
    assert ctx.turn_id is None
    assert ctx.state == {}
    assert ctx.type_encoders is None
    assert ctx.owner_key() is None


def test_tool_context_owner_key() -> None:
    """Verify owner_key formatting with various combinations of tenant and user."""
    assert ToolContext().owner_key() is None
    assert ToolContext(user="alice").owner_key() == "default:alice"
    assert ToolContext(tenant_id="t1", user="bob").owner_key() == "t1:bob"

    mock_user = MagicMock()
    mock_user.id = "user-123"
    assert ToolContext(tenant_id="acme", user=mock_user).owner_key() == "acme:user-123"

    mock_user_pk = MagicMock(spec=[])
    mock_user_pk.pk = "pk-456"
    assert ToolContext(tenant_id="acme", user=mock_user_pk).owner_key() == "acme:pk-456"


def test_tool_context_from_connection_empty_scope() -> None:
    """Verify ToolContext extraction from an empty ASGI connection scope."""
    mock_conn = MagicMock()
    mock_conn.scope = {}
    mock_conn.state = MagicMock()
    mock_conn.headers = {}
    mock_conn.user = None
    mock_conn.auth = None
    mock_conn.session = {}
    mock_conn.route_handler = MagicMock(spec=[])

    ctx = ToolContext.from_connection(mock_conn)
    assert ctx.request is mock_conn
    assert ctx.user is None
    assert ctx.tenant_id is None
    assert ctx.session_id is None


def test_tool_context_from_connection_with_auth_and_session() -> None:
    """Verify ToolContext extracts user, tenant, session, and encoders from connection."""
    mock_conn = MagicMock()
    mock_conn.scope = {"tenant_id": "tenant-xyz"}
    mock_conn.state = MagicMock()
    mock_conn.headers = {"x-test": "val"}
    mock_conn.user = "user-abc"
    mock_conn.auth = "bearer-token"
    mock_conn.session = {"session_id": "sess-999"}

    mock_route = MagicMock()
    mock_route.resolve_type_encoders.return_value = {int: str}
    mock_conn.route_handler = mock_route

    ctx = ToolContext.from_connection(mock_conn)
    assert ctx.request is mock_conn
    assert ctx.user == "user-abc"
    assert ctx.tenant_id == "tenant-xyz"
    assert ctx.session_id == "sess-999"
    assert ctx.type_encoders == {int: str}
    assert ctx.owner_key() == "tenant-xyz:user-abc"
