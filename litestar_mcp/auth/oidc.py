"""OIDC bearer-token validation + injectable JWKS cache.

This module is the single home for all OIDC machinery in litestar-mcp:

- Public :func:`create_oidc_validator` factory — wraps the internal
  :func:`_validate_oidc_bearer` into a closure-shaped async callable
  that :class:`~litestar_mcp.auth.MCPAuthBackend` accepts.
- :class:`JWKSCache` protocol + :class:`DefaultJWKSCache` implementation
  for injectable JWKS / discovery document caches.
- Internal validation helpers (``_fetch_json_document``, ``_resolve_jwks``,
  ``_load_signing_key``, ``_validate_oidc_bearer``,
  ``_validate_with_oidc_provider``) consumed by
  :class:`~litestar_mcp.auth.MCPAuthBackend`.

Before v0.5.0 the cache, private helpers, and public factory lived in
separate modules (``auth/_cache.py``, ``auth/_oidc.py``, ``auth/oidc.py``).
Ch5 of the v0.5.0 roadmap flattens them here.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, Protocol, cast, runtime_checkable

import httpx
import jwt
from jwt import algorithms as jwt_algorithms
from litestar.serialization import encode_json

_logger = logging.getLogger(__name__)

__all__ = (
    "DefaultJWKSCache",
    "JWKSCache",
    "TokenValidator",
    "create_oidc_validator",
    "get_default_cache",
    "reset_default_cache",
)

ValidationErrorHook = Callable[[str, BaseException], "None | Awaitable[None]"]
"""Observability callback: ``(issuer, exception) -> None | Awaitable[None]``.

Invoked when a provider validator rejects a token or raises during
validation. Return value is ignored. Sync or async; litestar-mcp
auto-detects. Exceptions raised by the hook itself are logged and
swallowed to keep auth outcomes independent of observability plumbing.
"""

TokenValidator = Callable[[str], Awaitable["dict[str, Any] | None"]]
"""Async callable: ``(token: str) -> dict[str, Any] | None``."""

DEFAULT_CLOCK_SKEW_SECONDS = 30
DEFAULT_JWKS_CACHE_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# JWKS cache protocol + default implementation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Internal OIDC validation helpers
# ---------------------------------------------------------------------------

# Per-URL single-flight locks. These are an implementation detail of the
# read-through wrapper (NOT the cache protocol) — concurrent cold readers on
# the same URL serialise here so only one ``_fetch_json_document`` call goes
# out across N waiters.
_FETCH_LOCKS: dict[str, asyncio.Lock] = {}


def _normalize_issuer(issuer: str) -> str:
    return issuer.rstrip("/")


def _default_discovery_url(issuer: str) -> str:
    return f"{_normalize_issuer(issuer)}/.well-known/openid-configuration"


async def _fetch_json_document(url: str) -> dict[str, Any]:
    """Fetch a JSON document from a remote URL."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())


async def _get_cached_json_document(
    url: str,
    cache_ttl: int,
    cache: JWKSCache | None = None,
) -> dict[str, Any]:
    """Read-through cache fetch with per-URL single-flight on cold misses."""
    resolved_cache = cache if cache is not None else get_default_cache()
    hit = await resolved_cache.get(url)
    if hit is not None:
        return hit

    lock = _FETCH_LOCKS.setdefault(url, asyncio.Lock())
    async with lock:
        hit = await resolved_cache.get(url)
        if hit is not None:
            return hit
        document = await _fetch_json_document(url)
        await resolved_cache.set(url, document, ttl=cache_ttl)
        return document


async def _resolve_jwks(
    issuer: str,
    *,
    jwks_uri: str | None,
    discovery_url: str | None,
    cache_ttl: int,
    cache: JWKSCache | None = None,
) -> dict[str, Any]:
    resolved_uri = jwks_uri
    if resolved_uri is None:
        discovery = await _get_cached_json_document(
            discovery_url or _default_discovery_url(issuer),
            cache_ttl,
            cache,
        )
        resolved_uri = cast("str", discovery["jwks_uri"])
    return await _get_cached_json_document(resolved_uri, cache_ttl, cache)


def _load_signing_key(token: str, jwks: dict[str, Any], algorithms: tuple[str, ...] | list[str]) -> Any:
    header = jwt.get_unverified_header(token)
    key_id = header.get("kid")
    header_alg = header.get("alg")
    if header_alg is None:
        msg = "JWT header is missing 'alg'"
        raise ValueError(msg)
    if header_alg not in algorithms:
        msg = f"JWT uses unsupported algorithm: {header_alg}"
        raise ValueError(msg)

    keys = jwks.get("keys", [])
    selected_key = None
    for key in keys:
        if key_id is None or key.get("kid") == key_id:
            selected_key = key
            break

    if selected_key is None:
        msg = "No matching JWKS signing key found"
        raise ValueError(msg)

    algorithm = jwt_algorithms.get_default_algorithms()[header_alg]
    return algorithm.from_jwk(encode_json(selected_key).decode("utf-8"))


async def _invoke_validation_error_hook(
    hook: ValidationErrorHook,
    issuer: str,
    exc: BaseException,
) -> None:
    try:
        result = hook(issuer, exc)
        if inspect.isawaitable(result):
            await result
    except Exception:
        _logger.exception("on_validation_error hook raised for issuer %s", issuer)


async def _validate_oidc_bearer(
    token: str,
    *,
    issuer: str,
    audience: str | list[str] | None,
    jwks_uri: str | None,
    discovery_url: str | None = None,
    algorithms: tuple[str, ...] | list[str],
    clock_skew: int = DEFAULT_CLOCK_SKEW_SECONDS,
    jwks_cache_ttl: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
    jwks_cache: JWKSCache | None = None,
    on_validation_error: ValidationErrorHook | None = None,
) -> dict[str, Any] | None:
    """Validate a bearer token against an OIDC issuer.

    Single source of truth for OIDC validation used by both
    :class:`~litestar_mcp.auth.OIDCProviderConfig`-driven enforcement and
    the public :func:`create_oidc_validator` factory.

    Returns:
        Validated claims dict or ``None`` if validation fails.
    """
    try:
        jwks = await _resolve_jwks(
            issuer,
            jwks_uri=jwks_uri,
            discovery_url=discovery_url,
            cache_ttl=jwks_cache_ttl,
            cache=jwks_cache,
        )
        signing_key = _load_signing_key(token, jwks, algorithms)
        return jwt.decode(
            token,
            signing_key,
            algorithms=list(algorithms),
            audience=audience,
            issuer=_normalize_issuer(issuer),
            leeway=clock_skew,
            options={"verify_aud": audience is not None},
        )
    except Exception as exc:  # noqa: BLE001
        if on_validation_error is not None:
            await _invoke_validation_error_hook(on_validation_error, issuer, exc)
        return None


async def _validate_with_oidc_provider(
    token: str,
    provider: Any,
    *,
    on_validation_error: ValidationErrorHook | None = None,
) -> dict[str, Any] | None:
    """Validate ``token`` against a single :class:`OIDCProviderConfig`."""
    return await _validate_oidc_bearer(
        token,
        issuer=provider.issuer,
        audience=provider.audience,
        jwks_uri=provider.jwks_uri,
        discovery_url=provider.discovery_url,
        algorithms=provider.algorithms,
        clock_skew=provider.clock_skew,
        jwks_cache_ttl=provider.cache_ttl,
        jwks_cache=provider.jwks_cache,
        on_validation_error=on_validation_error,
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_oidc_validator(
    issuer: str,
    audience: str,
    *,
    jwks_uri: str | None = None,
    algorithms: tuple[str, ...] = ("RS256",),
    clock_skew: int = DEFAULT_CLOCK_SKEW_SECONDS,
    jwks_cache_ttl: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
    jwks_cache: JWKSCache | None = None,
    on_validation_error: ValidationErrorHook | None = None,
) -> TokenValidator:
    """Build an async token validator that verifies bearer tokens against an OIDC IdP.

    If ``jwks_uri`` is omitted, the validator auto-discovers it from
    ``{issuer}/.well-known/openid-configuration``. The JWKS document is
    cached in-memory with the given TTL.

    Args:
        issuer: Expected ``iss`` claim and discovery base URL.
        audience: Expected ``aud`` claim.
        jwks_uri: Optional explicit JWKS endpoint (overrides discovery).
        algorithms: Allowed JWS algorithms.
        clock_skew: Tolerance in seconds for ``exp`` / ``iat`` / ``nbf`` checks.
        jwks_cache_ttl: JWKS / discovery document TTL in seconds.
        jwks_cache: Optional shared :class:`JWKSCache` instance. When
            ``None`` the process-wide default cache is used.
        on_validation_error: Observability hook invoked on failure.

    Returns:
        An async callable suitable for
        :attr:`~litestar_mcp.auth.MCPAuthBackend`'s ``token_validator``
        constructor argument.

    Example:
        >>> from litestar.middleware import DefineMiddleware
        >>> from litestar_mcp import MCPAuthBackend, create_oidc_validator
        >>> validator = create_oidc_validator(
        ...     "https://company.okta.com",
        ...     "api://mcp-tools",
        ...     clock_skew=60,
        ... )
        >>> middleware = DefineMiddleware(MCPAuthBackend, token_validator=validator)
    """

    async def _validator(token: str) -> dict[str, Any] | None:
        return await _validate_oidc_bearer(
            token,
            issuer=issuer,
            audience=audience,
            jwks_uri=jwks_uri,
            algorithms=algorithms,
            clock_skew=clock_skew,
            jwks_cache_ttl=jwks_cache_ttl,
            jwks_cache=jwks_cache,
            on_validation_error=on_validation_error,
        )

    return _validator
