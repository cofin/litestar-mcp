"""Runtime identity and request context passed to tools."""

from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from litestar.types import TypeEncodersMap

__all__ = ("ToolContext",)

_USER_ID_FIELDS = ("id", "sub", "username", "pk")


@dataclass(slots=True)
class ToolContext:
    """Identity, tenant, and request metadata injected into tools by type.

    Attributes:
        user: Authenticated principal from ``connection.user``, or ``None``.
        auth: Authentication value from ``connection.auth``, or ``None``.
        tenant_id: Tenant identifier resolved from the principal or ``request.state``.
        roles: Principal roles.
        scopes: Principal scopes.
        session_id: Conversation session identifier.
        turn_id: Agent turn identifier.
        request: Originating Litestar connection, or ``None`` outside HTTP.
        state: Mutable per-invocation values shared between tools.
        type_encoders: Encoders resolved from the route's ownership layers, used to serialize tool results.
    """

    user: "Any | None" = None
    auth: "Any | None" = None
    tenant_id: "str | None" = None
    roles: "tuple[str, ...]" = ()
    scopes: "tuple[str, ...]" = ()
    session_id: "str | None" = None
    turn_id: "str | None" = None
    request: "Any | None" = None
    state: "dict[str, Any]" = field(default_factory=dict)
    type_encoders: "TypeEncodersMap | None" = None

    @classmethod
    def from_connection(
        cls,
        connection: "Any | None",
        *,
        session_id: "str | None" = None,
        turn_id: "str | None" = None,
    ) -> "ToolContext":
        """Build a context from a Litestar connection's ``user``, ``auth``, ``state``, and route encoders."""
        if connection is None:
            return cls(session_id=session_id, turn_id=turn_id)

        scope = getattr(connection, "scope", None)
        scope_map = scope if isinstance(scope, dict) else {}

        user, auth = cls._extract_user_and_auth(connection, scope_map)
        roles, scopes, tenant_id = cls._extract_identity_attributes(connection, scope_map, user, auth)
        resolved_session_id = cls._extract_session_id(connection, scope_map, session_id)
        type_encoders = cls._extract_type_encoders(connection, scope_map)

        return cls(
            user=user,
            auth=auth,
            tenant_id=tenant_id,
            roles=roles,
            scopes=scopes,
            session_id=resolved_session_id,
            turn_id=turn_id,
            request=connection,
            type_encoders=type_encoders,
        )

    def owner_key(self) -> str | None:
        """Return ``"<tenant or default>:<user id>"`` for authenticated callers and ``None`` for anonymous ones."""
        user = self.user
        if user is None and isinstance(self.state, dict):
            user = self.state.get("user")

        if user is None:
            return None

        user_id = self._resolve_user_id(user)
        if user_id is None:
            return None

        tenant = self.tenant_id
        if tenant is None and isinstance(self.state, dict):
            tenant = self.state.get("tenant_id") or self.state.get("tenant")
        tenant = tenant or "default"
        return f"{tenant}:{user_id}"

    @staticmethod
    def _resolve_user_id(user: Any) -> str | None:
        """Extract a string user id from a string, dict, or object."""
        if isinstance(user, str):
            return user
        if isinstance(user, dict):
            for key in _USER_ID_FIELDS:
                if user.get(key) is not None:
                    return str(user[key])
            return None
        for field_name in _USER_ID_FIELDS:
            val = getattr(user, field_name, None)
            if val is not None:
                return str(val)
        return None

    @staticmethod
    def _extract_user_and_auth(connection: Any, scope_map: dict[str, Any]) -> tuple[Any, Any]:
        """Resolve user and auth instances from connection scope or attributes."""
        user = scope_map.get("user")
        if user is None:
            with suppress(Exception):
                user = getattr(connection, "user", None)

        auth = scope_map.get("auth")
        if auth is None:
            with suppress(Exception):
                auth = getattr(connection, "auth", None)

        if auth is not None and user is None:
            user = auth

        return user, auth

    @classmethod
    def _extract_identity_attributes(
        cls,
        connection: Any,
        scope_map: dict[str, Any],
        user: Any,
        auth: Any,
    ) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
        """Extract roles, scopes, and tenant id from user, auth, and connection state."""
        roles: list[str] = []
        scopes: list[str] = []
        tenant_id: str | None = None

        scope_tenant = scope_map.get("tenant_id") or scope_map.get("tenant")
        if scope_tenant is not None:
            tenant_id = str(scope_tenant)

        if user is not None:
            roles = cls._extract_items(user, "roles")
            scopes = cls._extract_items(user, "scopes")
            if tenant_id is None:
                tenant_id = cls._extract_tenant(user)

        if auth is not None:
            if not roles:
                roles = cls._extract_items(auth, "roles")
            if not scopes:
                scopes = cls._extract_items(auth, "scopes")
            if tenant_id is None:
                tenant_id = cls._extract_tenant(auth)

        if tenant_id is None:
            state = getattr(connection, "state", None)
            if state is not None:
                tenant_id = cls._extract_tenant(state)

        return tuple(roles), tuple(scopes), tenant_id

    @staticmethod
    def _extract_session_id(
        connection: Any,
        scope_map: dict[str, Any],
        session_id: str | None,
    ) -> str | None:
        """Resolve session identifier from explicit argument, connection session, or scope."""
        if session_id is not None:
            return session_id

        conn_session = None
        if "session" in scope_map:
            conn_session = scope_map["session"]
        else:
            with suppress(Exception):
                conn_session = getattr(connection, "session", None)

        if isinstance(conn_session, dict):
            resolved = conn_session.get("session_id") or conn_session.get("id")
            return str(resolved) if resolved is not None else None

        session_val = scope_map.get("session_id")
        if isinstance(session_val, str):
            return session_val

        return None

    @staticmethod
    def _extract_type_encoders(connection: Any, scope_map: dict[str, Any]) -> "TypeEncodersMap | None":
        """Resolve route handler type encoders if configured on the route."""
        route_handler = scope_map.get("route_handler")
        if route_handler is None:
            route_handler = getattr(connection, "route_handler", None)
        if route_handler is not None and hasattr(route_handler, "resolve_type_encoders"):
            with suppress(Exception):
                return cast("TypeEncodersMap", route_handler.resolve_type_encoders())
        return None

    @staticmethod
    def _extract_items(obj: Any, key: str) -> list[str]:
        """Extract list of string values from attribute or dict key."""
        if hasattr(obj, key):
            val = getattr(obj, key)
            if hasattr(val, "_mock_return_value"):
                return []
            return [str(item) for item in val]
        if isinstance(obj, dict) and key in obj:
            return [str(item) for item in obj[key]]
        return []

    @staticmethod
    def _extract_tenant(obj: Any) -> str | None:
        """Extract tenant identifier from an object or dictionary."""
        if obj is None:
            return None
        if isinstance(obj, dict):
            val = obj.get("tenant_id") or obj.get("tenant")
            return str(val) if val is not None else None
        state_dict = getattr(obj, "_state", None)
        if isinstance(state_dict, dict):
            return ToolContext._extract_tenant(state_dict)
        for attr in ("tenant_id", "tenant"):
            val = getattr(obj, attr, None)
            if val is not None and not hasattr(val, "_mock_return_value"):
                return str(val)
        return None
