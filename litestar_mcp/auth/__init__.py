"""MCP authentication integration.

Exports:

- :class:`MCPAuthBackend` — the built-in
  :class:`~litestar.middleware.authentication.AbstractAuthenticationMiddleware`
  that validates bearer tokens against OIDC providers and/or a custom
  ``token_validator`` and populates ``connection.user`` / ``.auth``.
- :class:`OIDCProviderConfig` — declarative OIDC/JWKS provider config.
- :class:`MCPAuthConfig` — pure metadata for the
  ``/.well-known/oauth-protected-resource`` manifest.
- :func:`create_oidc_validator` — composable async bearer-token validator
  factory, usable as ``MCPAuthBackend(token_validator=...)``.
- :data:`TokenValidator` — type alias for the async validator signature.
"""

from __future__ import annotations

from litestar_mcp.auth.backend import MCPAuthBackend
from litestar_mcp.auth.config import MCPAuthConfig, OIDCProviderConfig
from litestar_mcp.auth.oidc import TokenValidator, create_oidc_validator

__all__ = (
    "MCPAuthBackend",
    "MCPAuthConfig",
    "OIDCProviderConfig",
    "TokenValidator",
    "create_oidc_validator",
)
