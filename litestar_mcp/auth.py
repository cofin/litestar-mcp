"""MCP OAuth 2.1 authentication and auth bridge."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, cast

from litestar.serialization import encode_json

_logger = logging.getLogger(__name__)

ValidationErrorHook = Callable[[str, BaseException], None | Awaitable[None]]
"""Observability callback: ``(issuer, exception) -> None | Awaitable[None]``.

Invoked when a provider validator rejects a token or raises during validation.
Return value is ignored. Sync or async; litestar-mcp auto-detects. Exceptions
raised by the hook itself are logged and swallowed to keep auth outcomes
independent of observability plumbing.
"""

_JSON_DOCUMENT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JSON_DOCUMENT_LOCKS: dict[str, asyncio.Lock] = {}

DEFAULT_CLOCK_SKEW_SECONDS = 30
DEFAULT_JWKS_CACHE_TTL_SECONDS = 3600


class MCPAuthHardRejectionError(Exception):
    """Raised by a ``token_validator`` to signal "I own this token and it is
    invalid; do not fall through to OIDC providers".

    The terminal HTTP response remains 401 per MCP / OAuth 2.1. This exception
    is an internal routing signal only; it never reaches the wire.
    """


@dataclass
class OIDCProviderConfig:
    """Configuration for validating bearer tokens against an OIDC/JWKS provider.

    Attributes:
        issuer: Expected ``iss`` claim and discovery base URL.
        audience: Expected ``aud`` claim (string, list, or ``None`` to skip).
        jwks_uri: Optional explicit JWKS endpoint (overrides discovery).
        discovery_url: Optional override for the OpenID discovery document URL.
        algorithms: Allowed JWS algorithms (default: ``["RS256"]``).
        cache_ttl: JWKS / discovery document cache TTL in seconds.
        clock_skew: Tolerance in seconds for ``exp`` / ``iat`` / ``nbf`` checks.
    """

    issuer: str
    audience: str | list[str] | None = None
    jwks_uri: str | None = None
    discovery_url: str | None = None
    algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    cache_ttl: int = DEFAULT_JWKS_CACHE_TTL_SECONDS
    clock_skew: int = DEFAULT_CLOCK_SKEW_SECONDS


@dataclass
class MCPAuthConfig:
    """Authentication configuration for MCP endpoints.

    When configured, the MCP endpoint requires a valid bearer token on all
    non-exempt requests (initialize/ping are exempt).

    Attributes:
        issuer: OAuth 2.1 authorization server issuer URL.
        audience: The resource identifier (used in protected resource metadata).
        scopes: Mapping of scope name to description (for documentation/metadata).
        token_validator: Async callable that validates a bearer token string and
            returns user claims dict if valid, or None if invalid. This is the
            pluggable hook that integrates with the app's existing auth backend.

            **Claims-dict contract:** the returned mapping is passed as-is to
            :attr:`user_resolver` (``user_resolver(claims, app)``) and is
            otherwise opaque to litestar-mcp — no keys are read or rewritten
            by the framework. Tool handlers do NOT see claims directly; they
            see the :attr:`user_resolver` return value injected as
            ``resolved_user``. Keys prefixed with ``_`` are reserved for
            downstream use and will be ignored by litestar-mcp in any future
            version.

            Raise :class:`MCPAuthHardRejectionError` to signal "I own this
            token and it is invalid" and skip provider fallthrough.
        providers: Optional built-in OIDC/JWKS validation providers. These are
            tried after ``token_validator`` if it declines the token.
        user_resolver: Optional callable that turns validated claims into a
            user object that is then exposed to MCP tool execution. Called as
            ``user_resolver(claims, app)``; the return value is injected as
            ``resolved_user`` into tool handlers that request it.
    """

    issuer: str | None = None
    audience: str | list[str] | None = None
    scopes: dict[str, str] | None = None
    token_validator: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None
    providers: list[OIDCProviderConfig] | None = None
    user_resolver: Callable[[dict[str, Any], Any], Awaitable[Any] | Any] | None = None
    on_validation_error: ValidationErrorHook | None = None
    """Optional observability hook fired on provider validation failure.

    Called with ``(issuer, exception)`` before the validator returns ``None``.
    Sync or async. Hook exceptions are logged and swallowed — auth outcome
    stays independent of observability.
    """


def _normalize_issuer(issuer: str) -> str:
    return issuer.rstrip("/")


def _default_discovery_url(issuer: str) -> str:
    return f"{_normalize_issuer(issuer)}/.well-known/openid-configuration"


async def _fetch_json_document(url: str) -> dict[str, Any]:
    """Fetch a JSON document from a remote URL."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - optional dependency path
        msg = "Built-in OIDC/JWKS validation requires the 'auth' extra with httpx installed."
        raise RuntimeError(msg) from exc

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())


async def _get_cached_json_document(url: str, cache_ttl: int) -> dict[str, Any]:
    # Fast path: warm cache hit avoids lock acquisition entirely.
    cache_entry = _JSON_DOCUMENT_CACHE.get(url)
    if cache_entry is not None and cache_entry[0] > monotonic():
        return cache_entry[1]

    # Single-flight: concurrent cold-cache callers for the same URL coalesce
    # into one upstream fetch. Distinct URLs use distinct locks and proceed
    # in parallel.
    lock = _JSON_DOCUMENT_LOCKS.setdefault(url, asyncio.Lock())
    async with lock:
        cache_entry = _JSON_DOCUMENT_CACHE.get(url)
        if cache_entry is not None and cache_entry[0] > monotonic():
            return cache_entry[1]
        document = await _fetch_json_document(url)
        _JSON_DOCUMENT_CACHE[url] = (monotonic() + cache_ttl, document)
        return document


def _iter_providers(auth_config: MCPAuthConfig) -> list[OIDCProviderConfig]:
    providers = list(auth_config.providers or [])
    if auth_config.issuer is not None:
        providers.append(
            OIDCProviderConfig(
                issuer=auth_config.issuer,
                audience=auth_config.audience,
            )
        )
    return providers


async def _resolve_jwks(
    issuer: str,
    *,
    jwks_uri: str | None,
    discovery_url: str | None,
    cache_ttl: int,
) -> dict[str, Any]:
    resolved_uri = jwks_uri
    if resolved_uri is None:
        discovery = await _get_cached_json_document(
            discovery_url or _default_discovery_url(issuer),
            cache_ttl,
        )
        resolved_uri = cast("str", discovery["jwks_uri"])
    return await _get_cached_json_document(resolved_uri, cache_ttl)


def _load_signing_key(token: str, jwks: dict[str, Any], algorithms: "tuple[str, ...] | list[str]") -> Any:
    try:
        import jwt
        from jwt import algorithms as jwt_algorithms
    except ImportError as exc:  # pragma: no cover - optional dependency path
        msg = "Built-in OIDC/JWKS validation requires the 'auth' extra with PyJWT installed."
        raise RuntimeError(msg) from exc

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
    algorithms: "tuple[str, ...] | list[str]",
    clock_skew: int = DEFAULT_CLOCK_SKEW_SECONDS,
    jwks_cache_ttl: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
    on_validation_error: ValidationErrorHook | None = None,
) -> dict[str, Any] | None:
    """Validate a bearer token against an OIDC issuer.

    This is the single source of truth for OIDC validation used by both
    :class:`OIDCProviderConfig`-driven enforcement and the public
    :func:`litestar_mcp.oidc.create_oidc_validator` factory.

    Args:
        token: Raw bearer token string.
        issuer: Expected ``iss`` claim and default discovery base.
        audience: Expected ``aud`` claim; ``None`` disables audience checks.
        jwks_uri: Optional explicit JWKS endpoint; if ``None`` auto-discovered.
        discovery_url: Optional override for the OpenID discovery document URL.
        algorithms: Allowed JWS algorithms.
        clock_skew: Tolerance in seconds for ``exp`` / ``iat`` / ``nbf``.
        jwks_cache_ttl: JWKS cache TTL in seconds.
        on_validation_error: Optional observability hook; fired with
            ``(issuer, exception)`` on any validation failure path. Sync or
            async. Raises from the hook are logged and swallowed.

    Returns:
        Validated claims dict or ``None`` if validation fails.
    """
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - optional dependency path
        msg = "Built-in OIDC/JWKS validation requires the 'auth' extra with PyJWT installed."
        raise RuntimeError(msg) from exc

    try:
        jwks = await _resolve_jwks(
            issuer,
            jwks_uri=jwks_uri,
            discovery_url=discovery_url,
            cache_ttl=jwks_cache_ttl,
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
    provider: OIDCProviderConfig,
    *,
    on_validation_error: ValidationErrorHook | None = None,
) -> dict[str, Any] | None:
    return await _validate_oidc_bearer(
        token,
        issuer=provider.issuer,
        audience=provider.audience,
        jwks_uri=provider.jwks_uri,
        discovery_url=provider.discovery_url,
        algorithms=provider.algorithms,
        clock_skew=provider.clock_skew,
        jwks_cache_ttl=provider.cache_ttl,
        on_validation_error=on_validation_error,
    )


async def validate_bearer_token(
    token: str,
    auth_config: MCPAuthConfig,
) -> dict[str, Any] | None:
    """Validate a bearer token using the configured validator(s).

    Args:
        token: The raw bearer token string.
        auth_config: The auth configuration with the validator.

    Returns:
        User claims dict if valid, None if invalid.
    """
    if auth_config.token_validator is not None:
        try:
            claims = await auth_config.token_validator(token)
        except MCPAuthHardRejectionError:
            return None
        if claims is not None:
            return claims

    for provider in _iter_providers(auth_config):
        claims = await _validate_with_oidc_provider(
            token,
            provider,
            on_validation_error=auth_config.on_validation_error,
        )
        if claims is not None:
            return claims

    return None


async def resolve_user(
    claims: dict[str, Any],
    auth_config: MCPAuthConfig,
    app: Any,
) -> Any:
    """Resolve a user object from validated claims."""
    if auth_config.user_resolver is None:
        return None

    result = auth_config.user_resolver(claims, app)
    if hasattr(result, "__await__"):
        return await result
    return result
