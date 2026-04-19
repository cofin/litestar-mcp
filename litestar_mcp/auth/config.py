"""Auth configuration dataclasses: OIDCProviderConfig + collapsed MCPAuthConfig."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from litestar_mcp.auth._oidc import (
    DEFAULT_CLOCK_SKEW_SECONDS,
    DEFAULT_JWKS_CACHE_TTL_SECONDS,
)

if TYPE_CHECKING:
    from litestar_mcp.auth._cache import JWKSCache


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
        jwks_cache: Optional shared :class:`~litestar_mcp.auth.JWKSCache`
            instance. When ``None`` the process-wide default cache is used,
            which matches 0.4.0 semantics. Providing a custom cache lets an
            application share one JWKS store across its own auth stack and
            litestar-mcp's OIDC validator.
    """

    issuer: str
    audience: str | list[str] | None = None
    jwks_uri: str | None = None
    discovery_url: str | None = None
    algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    cache_ttl: int = DEFAULT_JWKS_CACHE_TTL_SECONDS
    clock_skew: int = DEFAULT_CLOCK_SKEW_SECONDS
    jwks_cache: JWKSCache | None = None


@dataclass
class MCPAuthConfig:
    """Metadata for the ``/.well-known/oauth-protected-resource`` manifest.

    Authentication *enforcement* is handled by a Litestar authentication
    middleware (either your own :class:`~litestar.middleware.authentication.AbstractAuthenticationMiddleware`
    subclass or the built-in :class:`~litestar_mcp.auth.MCPAuthBackend`).
    This struct only describes the auth surface to discovery clients.

    Attributes:
        issuer: OAuth 2.1 authorization server issuer URL (advertised to clients).
        audience: Resource identifier used in the protected-resource manifest.
        scopes: Mapping of scope name to human-readable description.
    """

    issuer: str | None = None
    audience: str | list[str] | None = None
    scopes: dict[str, str] | None = None
