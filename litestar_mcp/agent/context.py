"""Runtime execution context for tools and agent operations."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any


def _extract_candidate(connection_or_request: Any, key: str) -> Any | None:
    """Safely extract a named attribute or ASGI scope value from a connection."""
    scope = getattr(connection_or_request, "scope", None)
    if isinstance(scope, dict) and key in scope:
        return scope.get(key)
    with suppress(Exception):
        return getattr(connection_or_request, key, None)
    return None


def _extract_user_info(
    connection_or_request: Any,
) -> tuple[Any | None, Any | None, str | None, list[str], list[str]]:
    """Extract user, auth, tenant_id, roles, and scopes from an ASGI connection."""
    user = _extract_candidate(connection_or_request, "user")
    auth = _extract_candidate(connection_or_request, "auth")
    tenant_id: str | None = None
    roles: list[str] = []
    scopes: list[str] = []

    if user is not None:
        if hasattr(user, "roles"):
            roles = list(user.roles)
        if hasattr(user, "scopes"):
            scopes = list(user.scopes)
        if hasattr(user, "tenant_id") and user.tenant_id is not None:
            tenant_id = str(user.tenant_id)

    if auth is not None:
        if user is None:
            user = auth
        if not roles and hasattr(auth, "roles"):
            roles = list(auth.roles)
        if not scopes and hasattr(auth, "scopes"):
            scopes = list(auth.scopes)
        if tenant_id is None and hasattr(auth, "tenant_id") and auth.tenant_id is not None:
            tenant_id = str(auth.tenant_id)

    return user, auth, tenant_id, roles, scopes


def _extract_session_id(connection_or_request: Any) -> str | None:
    """Extract session ID from an ASGI connection or session mapping."""
    session = _extract_candidate(connection_or_request, "session")
    if isinstance(session, dict):
        return session.get("session_id") or session.get("id")
    return None


def _extract_tenant_id_from_state(connection_or_request: Any) -> str | None:
    """Extract tenant ID from application or request state."""
    state = getattr(connection_or_request, "state", None)
    if state is not None:
        if hasattr(state, "tenant_id") and state.tenant_id is not None:
            return str(state.tenant_id)
        if hasattr(state, "tenant") and state.tenant is not None:
            return str(state.tenant)
    return None


@dataclass(slots=True)
class ToolContext:
    """Runtime execution context provided to tools and agent operations.

    Carries identity, tenant boundaries, and request metadata, resolved
    from Litestar Security or request context without leaking into LLM schemas.
    """

    user: Any | None = None
    tenant_id: str | None = None
    auth: Any | None = None
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    session_id: str | None = None
    turn_id: str = ""
    request: Any | None = None
    state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from context state or metadata."""
        if key in self.state:
            return self.state[key]
        return self.metadata.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store a value in context state and metadata."""
        self.state[key] = value
        self.metadata[key] = value

    @classmethod
    def from_connection(
        cls,
        connection: Any = None,
        session_id: str | None = None,
        turn_id: str = "",
    ) -> ToolContext:
        """Construct a ToolContext from a Litestar Request or ASGIConnection."""
        if connection is None:
            return cls(session_id=session_id, turn_id=turn_id)

        user, auth, tenant_id, roles, scopes = _extract_user_info(connection)
        if tenant_id is None:
            tenant_id = _extract_tenant_id_from_state(connection)
        resolved_session_id = session_id if session_id is not None else _extract_session_id(connection)

        return cls(
            user=user,
            tenant_id=tenant_id,
            auth=auth,
            roles=roles,
            scopes=scopes,
            session_id=resolved_session_id,
            turn_id=turn_id,
            request=connection,
        )


def resolve_tool_context(
    connection_or_request: Any = None,
    *,
    session_id: str | None = None,
    turn_id: str = "",
) -> ToolContext:
    """Resolve a ToolContext from an active Litestar connection, request, or state.

    Gracefully detects litestar-security Principal and CurrentUser when installed,
    falling back to generic request identity.
    """
    return ToolContext.from_connection(
        connection_or_request,
        session_id=session_id,
        turn_id=turn_id,
    )


__all__ = (
    "ToolContext",
    "resolve_tool_context",
)
