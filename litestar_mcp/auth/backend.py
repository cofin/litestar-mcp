"""MCPAuthBackend + auth configuration dataclasses.

This module consolidates the built-in bearer/OIDC authentication
middleware (:class:`MCPAuthBackend`) with the configuration dataclasses
that describe OIDC providers (:class:`OIDCProviderConfig`) and the
protected-resource discovery manifest (:class:`MCPAuthConfig`). Before
v0.5.0 these lived in separate modules; they are now consolidated here.
"""

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litestar.exceptions import NotAuthorizedException
from litestar.middleware.authentication import (
    AbstractAuthenticationMiddleware,
    AuthenticationResult,
)

from litestar_mcp.auth.oidc import (
    DEFAULT_CLOCK_SKEW_SECONDS,
    DEFAULT_JWKS_CACHE_TTL_SECONDS,
    _validate_with_oidc_provider,
)

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.types import ASGIApp, Method, Scopes

    from litestar_mcp.auth.oidc import JWKSCache

__all__ = (
    "MCPAuthBackend",
    "MCPAuthConfig",
    "OIDCProviderConfig",
    "TokenValidatorFn",
    "UserResolver",
)

UserResolver = Callable[["dict[str, Any]", Any], "Awaitable[Any] | Any"]
"""Callable ``(claims, app) -> user``; sync or async."""

TokenValidatorFn = Callable[[str], "Awaitable[dict[str, Any] | None]"]
"""Async callable ``(token) -> claims | None``; returning ``None`` declines the token."""

_BEARER_PREFIX = "Bearer "
_DEFAULT_HEADER_NAME = "Authorization"
_INVALID_TOKEN_MSG = "Invalid token"  # noqa: S105 — auth failure message, not a credential


# Configuration dataclasses


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
            instance. When ``None`` the process-wide default cache is used.
    """

    issuer: "str"
    audience: "str | list[str] | None" = None
    jwks_uri: "str | None" = None
    discovery_url: "str | None" = None
    algorithms: "list[str]" = field(default_factory=lambda: ["RS256"])
    cache_ttl: "int" = DEFAULT_JWKS_CACHE_TTL_SECONDS
    clock_skew: "int" = DEFAULT_CLOCK_SKEW_SECONDS
    jwks_cache: "JWKSCache | None" = None


@dataclass
class MCPAuthConfig:
    """Metadata for the ``/.well-known/oauth-protected-resource`` manifest.

    Authentication *enforcement* is handled by a Litestar authentication
    middleware (either your own
    :class:`~litestar.middleware.authentication.AbstractAuthenticationMiddleware`
    subclass or the built-in :class:`MCPAuthBackend`). This struct only
    describes the auth surface to discovery clients.

    Attributes:
        issuer: OAuth 2.1 authorization server issuer URL (advertised to clients).
        audience: Resource identifier used in the protected-resource manifest.
        scopes: Mapping of scope name to human-readable description.
    """

    issuer: "str | None" = None
    audience: "str | list[str] | None" = None
    scopes: "dict[str, str] | None" = None


# Authentication middleware


class MCPAuthBackend(AbstractAuthenticationMiddleware):
    """Authenticate bearer tokens via OIDC providers + optional custom validator.

    Registration::

        from litestar import Litestar
        from litestar.middleware import DefineMiddleware
        from litestar_mcp import MCPAuthBackend, OIDCProviderConfig

        app = Litestar(
            middleware=[
                DefineMiddleware(
                    MCPAuthBackend,
                    providers=[OIDCProviderConfig(issuer="https://idp", audience="api")],
                    user_resolver=lambda claims, app: MyUser(sub=claims["sub"]),
                ),
            ],
        )

    Apps that already ship their own
    :class:`~litestar.middleware.authentication.AbstractAuthenticationMiddleware`
    (DMA's ``IAPAuthenticationMiddleware``, Litestar's JWT backends, etc.) do
    not need this — MCP route handlers read ``request.user`` / ``request.auth``
    populated by whichever middleware the app installed.

    ``header_name`` / ``token_prefix`` make the built-in validation engine
    usable behind identity proxies that inject a verified JWT in a
    non-standard header. For Google Cloud IAP the assertion arrives raw (no
    ``Bearer`` prefix) in ``X-Goog-IAP-JWT-Assertion``::

        DefineMiddleware(
            MCPAuthBackend,
            providers=[OIDCProviderConfig(issuer="https://cloud.google.com/iap", audience="/projects/.../apps/...")],
            header_name="X-Goog-IAP-JWT-Assertion",
            token_prefix="",
        )
    """

    def __init__(
        self,
        app: "ASGIApp",
        providers: "Sequence[OIDCProviderConfig]" = (),
        token_validator: "TokenValidatorFn | None" = None,
        user_resolver: "UserResolver | None" = None,
        header_name: "str" = _DEFAULT_HEADER_NAME,
        token_prefix: "str" = _BEARER_PREFIX,
        exclude: "str | list[str] | None" = None,
        exclude_from_auth_key: "str" = "exclude_from_auth",
        exclude_http_methods: "Sequence[Method] | None" = None,
        scopes: "Scopes | None" = None,
    ) -> "None":
        super().__init__(
            app=app,
            exclude=exclude,
            exclude_from_auth_key=exclude_from_auth_key,
            exclude_http_methods=exclude_http_methods,
            scopes=scopes,
        )
        self.providers = tuple(providers)
        self.token_validator = token_validator
        self.user_resolver = user_resolver
        self.header_name = header_name
        self.token_prefix = token_prefix
        # ``connection.headers.get`` is case-insensitive; keep the original
        # casing for the challenge scheme and error message.
        self._challenge = token_prefix.strip() or "Bearer"

    async def authenticate_request(self, connection: "ASGIConnection[Any, Any, Any, Any]") -> "AuthenticationResult":
        header_value = connection.headers.get(self.header_name, "")
        token = self._extract_token(header_value)
        if token is None:
            msg = f"Missing or invalid {self.header_name} header"
            raise NotAuthorizedException(
                msg,
                headers={"WWW-Authenticate": self._challenge},
            )

        claims = await self._validate(token)
        if claims is None:
            raise NotAuthorizedException(
                _INVALID_TOKEN_MSG,
                headers={"WWW-Authenticate": self._challenge},
            )

        user = await self._resolve_user(claims, connection.app) if self.user_resolver is not None else None
        return AuthenticationResult(user=user, auth=claims)

    def _extract_token(self, header_value: "str") -> "str | None":
        """Strip the configured prefix from ``header_value``; ``None`` if absent.

        An empty ``token_prefix`` treats the whole header value as the token
        (e.g. GCP IAP), so an empty header is reported as a missing header
        rather than falling through to an ``Invalid token`` error.
        """
        if self.token_prefix and not header_value.startswith(self.token_prefix):
            return None
        token = header_value[len(self.token_prefix) :]
        return token or None

    async def _validate(self, token: "str") -> "dict[str, Any] | None":
        if self.token_validator is not None:
            claims = await self.token_validator(token)
            if claims is not None:
                return claims
        for provider in self.providers:
            claims = await _validate_with_oidc_provider(token, provider)
            if claims is not None:
                return claims
        return None

    async def _resolve_user(self, claims: "dict[str, Any]", app: "Any") -> "Any":
        assert self.user_resolver is not None  # noqa: S101 - guarded by caller
        result = self.user_resolver(claims, app)
        if inspect.isawaitable(result):
            return await result
        return result
