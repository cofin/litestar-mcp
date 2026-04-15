"""MCP session manager backed by a pluggable Litestar Store.

This module implements the spec-compliant Streamable HTTP session
lifecycle for MCP: sessions are created on ``initialize``, identified
by an opaque ``Mcp-Session-Id`` header, persisted in a pluggable
:class:`litestar.stores.base.Store`, and deleted on ``DELETE /mcp``.

In-process stream bookkeeping (SSE queues, Last-Event-ID replay) lives
in :mod:`litestar_mcp.sse`; this module is concerned only with the
identity/state half of the protocol so it can ride on any Store
backend (memory, Redis, SQLAlchemy/advanced_alchemy, SQLSpec, etc).
"""

import secrets
import time
from typing import Any

import msgspec
from litestar.serialization import decode_json, encode_json
from litestar.stores.base import Store

__all__ = ("MCPSession", "MCPSessionManager", "SessionTerminated")


class MCPSession(msgspec.Struct, kw_only=True):
    """Persisted MCP session state.

    Only serializable, cross-process state lives here. In-process
    resources (SSE queues, background tasks) are tracked elsewhere.

    Attributes:
        id: Opaque session identifier carried in ``Mcp-Session-Id``.
        protocol_version: Negotiated MCP protocol version from ``initialize``.
        client_info: ``clientInfo`` object sent by the client during ``initialize``.
        capabilities: Negotiated capability flags.
        initialized: ``True`` after ``notifications/initialized`` is received.
        created_at: Wall-clock timestamp (``time.time()``) at creation.
        last_activity: Wall-clock timestamp of the last manager touch.
    """

    id: str
    protocol_version: str
    created_at: float
    last_activity: float
    client_info: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    initialized: bool = False


class SessionTerminated(Exception):
    """Raised when a session id is unknown, expired, or deleted."""


class MCPSessionManager:
    """Header-driven MCP session lifecycle on top of a Litestar Store.

    Sessions are persisted via the injected :class:`~litestar.stores.base.Store`
    and expire via the Store's TTL mechanism. Each successful
    :meth:`get` renews the TTL, so sessions stay alive while the client
    is actively issuing requests.

    In-process stream associations (SSE fan-out, Last-Event-ID replay)
    live in :class:`~litestar_mcp.sse.SSEManager`; this manager is
    purely persistence-layer.

    Deployment note:
        For multi-replica deployments (Cloud Run, Kubernetes, etc.),
        configure ``MCPConfig.session_store`` with a shared backend
        (Redis, SQLAlchemy via advanced_alchemy, SQLSpec) and configure
        your load balancer for sticky routing on the ``Mcp-Session-Id``
        header so in-process SSE streams land on the replica that
        opened them. The deploy-docs chapter of the v0.4.0 release-prep
        PRD ships the full prose and example manifests.
    """

    def __init__(self, store: Store, *, max_idle_seconds: float = 3600.0) -> None:
        """Initialize the session manager.

        Args:
            store: A Litestar ``Store`` instance (Memory, Redis, SQLAlchemy,
                etc.). The manager stores one JSON blob per session id.
            max_idle_seconds: Idle TTL (seconds) applied on create/get.
                Every ``get()`` with ``touch=True`` renews the TTL.
        """
        self._store = store
        self._max_idle_seconds = max_idle_seconds

    @staticmethod
    def _generate_id() -> str:
        return secrets.token_urlsafe(24)

    def _ttl(self) -> int:
        return int(self._max_idle_seconds)

    async def create(
        self,
        *,
        protocol_version: str,
        client_info: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> MCPSession:
        """Create and persist a new session.

        Args:
            protocol_version: Negotiated MCP protocol version.
            client_info: Client identity block from ``initialize``.
            capabilities: Negotiated capability flags.

        Returns:
            The newly created :class:`MCPSession`.
        """
        now = time.time()
        session = MCPSession(
            id=self._generate_id(),
            protocol_version=protocol_version,
            client_info=client_info or {},
            capabilities=capabilities or {},
            created_at=now,
            last_activity=now,
        )
        await self._store.set(session.id, encode_json(session), expires_in=self._ttl())
        return session

    async def get(self, session_id: str, *, touch: bool = True) -> MCPSession:
        """Fetch a session, renewing its TTL by default.

        Args:
            session_id: The opaque ``Mcp-Session-Id`` value.
            touch: When ``True`` (default) renew the Store TTL.

        Returns:
            The live :class:`MCPSession`.

        Raises:
            SessionTerminated: If the id is unknown or the entry has expired.
        """
        renew = self._ttl() if touch else None
        raw = await self._store.get(session_id, renew_for=renew)
        if raw is None:
            raise SessionTerminated(session_id)
        return msgspec.convert(decode_json(raw), MCPSession)

    async def mark_initialized(self, session_id: str) -> None:
        """Flip ``initialized`` to ``True`` and persist.

        Args:
            session_id: The session to mark as fully initialized.

        Raises:
            SessionTerminated: If the session is unknown/expired.
        """
        session = await self.get(session_id)
        session.initialized = True
        session.last_activity = time.time()
        await self._store.set(session.id, encode_json(session), expires_in=self._ttl())

    async def touch(self, session_id: str) -> MCPSession:
        """Update ``last_activity`` and persist. Returns the session."""
        session = await self.get(session_id)
        session.last_activity = time.time()
        await self._store.set(session.id, encode_json(session), expires_in=self._ttl())
        return session

    async def delete(self, session_id: str) -> None:
        """Remove a session from the Store. Idempotent."""
        await self._store.delete(session_id)
