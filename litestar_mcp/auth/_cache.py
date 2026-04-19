"""JWKS / OIDC discovery document cache protocol and default implementation.

:class:`JWKSCache` is the injection seam applications use when they already
run their own JWKS cache and want litestar-mcp's OIDC validator to share it.
:class:`DefaultJWKSCache` preserves the 0.4.0 module-global semantics (TTL
stamped at ``set``, per-URL :class:`asyncio.Lock` around writes) while
making the cache a first-class, injectable object.

Single-flight semantics (one network fetch across N concurrent cache-miss
readers on the same URL) are managed by the read-through helper in
:mod:`litestar_mcp.auth._oidc`, not by this module — that keeps the
``JWKSCache`` protocol narrow (``get`` / ``set`` / ``invalidate``) so
downstream caches (Redis, memcached, custom) only implement three methods.
"""

import asyncio
from time import monotonic
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class JWKSCache(Protocol):
    """Shared JWKS / OIDC discovery document cache contract.

    ``get`` returns the cached document (fresh, within TTL) or ``None``.
    ``set`` stamps an expiry at insertion time using ``ttl`` seconds.
    ``invalidate`` drops a single entry; implementations may treat
    ``invalidate`` as a no-op for unknown URLs.
    """

    async def get(self, url: str) -> dict[str, Any] | None: ...
    async def set(self, url: str, document: dict[str, Any], *, ttl: int) -> None: ...
    async def invalidate(self, url: str) -> None: ...


class DefaultJWKSCache:
    """In-process TTL cache with per-URL write locks.

    Preserves the 0.4.0 module-global cache semantics exactly — the public
    interface makes them injectable so applications can share one cache
    across their own auth stack and litestar-mcp's OIDC validator.
    """

    __slots__ = ("_locks", "_store")

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, url: str) -> asyncio.Lock:
        return self._locks.setdefault(url, asyncio.Lock())

    async def get(self, url: str) -> dict[str, Any] | None:
        entry = self._store.get(url)
        if entry is None:
            return None
        expires_at, document = entry
        if expires_at <= monotonic():
            return None
        return document

    async def set(self, url: str, document: dict[str, Any], *, ttl: int) -> None:
        async with self._lock_for(url):
            self._store[url] = (monotonic() + ttl, document)

    async def invalidate(self, url: str) -> None:
        async with self._lock_for(url):
            self._store.pop(url, None)

    def clear(self) -> None:
        """Drop every cached entry and all per-URL locks."""
        self._store.clear()
        self._locks.clear()


_default_cache = DefaultJWKSCache()


def get_default_cache() -> DefaultJWKSCache:
    """Return the process-wide default cache instance."""
    return _default_cache


def reset_default_cache() -> None:
    """Clear the process-wide default cache (test hook)."""
    _default_cache.clear()
