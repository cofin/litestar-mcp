"""MCP OAuth 2.1 authentication and auth bridge."""

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional


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
    """

    issuer: Optional[str] = None
    audience: Optional[str] = None
    scopes: Optional[dict[str, str]] = None
    token_validator: Optional[Callable[[str], Coroutine[Any, Any, Optional[dict[str, Any]]]]] = None


async def validate_bearer_token(
    token: str,
    auth_config: MCPAuthConfig,
) -> Optional[dict[str, Any]]:
    """Validate a bearer token using the configured validator.

    Args:
        token: The raw bearer token string.
        auth_config: The auth configuration with the validator.

    Returns:
        User claims dict if valid, None if invalid.
    """
    if auth_config.token_validator is None:
        return None
    return await auth_config.token_validator(token)
