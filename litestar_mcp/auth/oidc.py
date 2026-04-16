"""Public OIDC validator factory for MCP tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from litestar_mcp.auth._oidc import (
    DEFAULT_CLOCK_SKEW_SECONDS,
    DEFAULT_JWKS_CACHE_TTL_SECONDS,
    ValidationErrorHook,
    _validate_oidc_bearer,
)

__all__ = ("TokenValidator", "create_oidc_validator")

TokenValidator = Callable[[str], Awaitable["dict[str, Any] | None"]]
"""Async callable: ``(token: str) -> dict[str, Any] | None``."""


def create_oidc_validator(
    issuer: str,
    audience: str,
    *,
    jwks_uri: str | None = None,
    algorithms: tuple[str, ...] = ("RS256",),
    clock_skew: int = DEFAULT_CLOCK_SKEW_SECONDS,
    jwks_cache_ttl: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
    on_validation_error: ValidationErrorHook | None = None,
) -> TokenValidator:
    """Build an async token validator that verifies bearer tokens against an OIDC IdP.

    If ``jwks_uri`` is omitted, the validator auto-discovers it from
    ``{issuer}/.well-known/openid-configuration``. The JWKS document is
    cached in-memory with the given TTL.

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
            on_validation_error=on_validation_error,
        )

    return _validator
