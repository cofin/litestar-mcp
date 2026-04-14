"""MCP OAuth 2.1 authentication and auth bridge."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, cast

_JSON_DOCUMENT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass
class OIDCProviderConfig:
    """Configuration for validating bearer tokens against an OIDC/JWKS provider."""

    issuer: str
    audience: str | list[str] | None = None
    jwks_uri: str | None = None
    discovery_url: str | None = None
    algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    cache_ttl: int = 300


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
        providers: Optional built-in OIDC/JWKS validation providers. These are
            tried after ``token_validator`` if it declines the token.
        user_resolver: Optional callable that turns validated claims into a
            user object that is then exposed to MCP tool execution.
    """

    issuer: str | None = None
    audience: str | list[str] | None = None
    scopes: dict[str, str] | None = None
    token_validator: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None
    providers: list[OIDCProviderConfig] | None = None
    user_resolver: Callable[[dict[str, Any], Any], Awaitable[Any] | Any] | None = None


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


async def _get_provider_jwks(provider: OIDCProviderConfig) -> dict[str, Any]:
    jwks_uri = provider.jwks_uri
    if jwks_uri is None:
        discovery = await _get_cached_json_document(
            provider.discovery_url or _default_discovery_url(provider.issuer),
            provider.cache_ttl,
        )
        jwks_uri = discovery["jwks_uri"]
    return await _get_cached_json_document(jwks_uri, provider.cache_ttl)


def _load_signing_key(token: str, jwks: dict[str, Any], algorithms: list[str]) -> Any:
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
    return algorithm.from_jwk(json.dumps(selected_key))


async def _validate_with_oidc_provider(token: str, provider: OIDCProviderConfig) -> dict[str, Any] | None:
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - optional dependency path
        msg = "Built-in OIDC/JWKS validation requires the 'auth' extra with PyJWT installed."
        raise RuntimeError(msg) from exc

    try:
        jwks = await _get_provider_jwks(provider)
        signing_key = _load_signing_key(token, jwks, provider.algorithms)
        return jwt.decode(
            token,
            signing_key,
            algorithms=provider.algorithms,
            audience=provider.audience,
            issuer=_normalize_issuer(provider.issuer),
            options={"verify_aud": provider.audience is not None},
        )
    except Exception:  # noqa: BLE001
        return None


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
        claims = await auth_config.token_validator(token)
        if claims is not None:
            return claims

    for provider in _iter_providers(auth_config):
        claims = await _validate_with_oidc_provider(token, provider)
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
