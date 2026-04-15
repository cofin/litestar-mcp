"""Shared bearer-auth helpers for the integration test matrix.

These helpers provide a deterministic, symmetric-HMAC (HS256) token flow
that the integration suites can share. Production code lives outside this
module; everything here is strictly for tests.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from litestar.connection import ASGIConnection
from litestar.security.jwt import OAuth2PasswordBearerAuth, Token

from litestar_mcp.auth import MCPAuthConfig

ISSUER = "https://auth.test.invalid"
AUDIENCE = "https://api.test.invalid"
ALGORITHMS = ["HS256"]
SECRET = "integration-test-secret-key-32-bytes-long!!"
TOKEN_URL = "/auth/token"


@dataclass
class AuthenticatedUser:
    """Resolved user exposed via ``request.user`` when bearer auth succeeds."""

    sub: str
    scopes: tuple[str, ...] = ()

    @property
    def id(self) -> str:  # pragma: no cover - trivial accessor
        """Return the subject identifier."""
        return self.sub


def mint_access_token(
    subject: str = "integration-user",
    scopes: list[str] | None = None,
    *,
    expires_in: int = 3600,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
) -> str:
    """Mint an HS256-signed bearer token for integration tests.

    Args:
        subject: The JWT ``sub`` claim.
        scopes: Optional list of scope strings embedded as the ``scopes`` claim.
        expires_in: Seconds until expiry (use negative for expired tokens).
        issuer: Override the issuer claim (tests use the default).
        audience: Override the audience claim (tests use the default).

    Returns:
        The encoded JWT string.
    """
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    if scopes is not None:
        payload["scopes"] = list(scopes)
    return jwt.encode(payload, SECRET, algorithm="HS256")


async def bearer_token_validator(token: str) -> dict[str, Any] | None:
    """Validate a bearer token and return the decoded claims dict or ``None``.

    Used as the ``token_validator`` callback on :class:`MCPAuthConfig`.
    """
    try:
        claims = jwt.decode(
            token,
            SECRET,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=ISSUER,
        )
    except jwt.PyJWTError:
        return None
    if not isinstance(claims, dict):  # pragma: no cover - defensive
        return None
    return claims


class BearerTokenValidator:
    """Callable wrapper mirroring the async validator signature."""

    __slots__ = ()

    async def __call__(self, token: str) -> dict[str, Any] | None:
        """Validate ``token`` and return its claims or ``None``."""
        return await bearer_token_validator(token)


async def _retrieve_user_handler(
    token: Token, _connection: ASGIConnection[Any, Any, Any, Any]
) -> AuthenticatedUser:
    """Resolve the bearer token into an :class:`AuthenticatedUser` instance."""
    extras = token.extras or {}
    scopes = extras.get("scopes") or []
    if not isinstance(scopes, list):
        scopes = []
    return AuthenticatedUser(sub=token.sub, scopes=tuple(str(s) for s in scopes))


def build_oauth_backend() -> OAuth2PasswordBearerAuth[AuthenticatedUser, Token]:
    """Build the Litestar OAuth2 bearer backend used by the integration apps.

    The backend is configured with:
      * the same HS256 secret as :func:`mint_access_token`
      * a ``retrieve_user_handler`` that resolves ``request.user``
      * exclude paths that cover the MCP endpoint and ``.well-known`` metadata
        so the plugin's own auth middleware — not the app backend — is the
        authoritative gate for those routes.
    """
    backend: OAuth2PasswordBearerAuth[AuthenticatedUser, Token] = OAuth2PasswordBearerAuth[
        AuthenticatedUser, Token
    ](
        token_secret=SECRET,
        token_url=TOKEN_URL,
        retrieve_user_handler=_retrieve_user_handler,
        algorithm="HS256",
        exclude=["^/mcp(/.*)?$", "^/.well-known/"],
    )
    return backend


async def _mcp_user_resolver(claims: dict[str, Any], _app: Any) -> AuthenticatedUser:
    """Resolve MCP-validated claims into the shared :class:`AuthenticatedUser`."""
    scopes = claims.get("scopes") or []
    if not isinstance(scopes, list):
        scopes = []
    return AuthenticatedUser(sub=str(claims.get("sub", "")), scopes=tuple(str(s) for s in scopes))


def build_mcp_auth_config() -> MCPAuthConfig:
    """Build the :class:`MCPAuthConfig` used when apps run in bearer mode."""
    return MCPAuthConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        token_validator=BearerTokenValidator(),
        user_resolver=_mcp_user_resolver,
    )


VALID_TOKEN = mint_access_token(subject="integration-user", scopes=["mcp:read", "mcp:write"])
EXPIRED_TOKEN = mint_access_token(
    subject="integration-user",
    scopes=["mcp:read"],
    expires_in=-60,
)
FORGED_TOKEN = jwt.encode(
    {
        "sub": "attacker",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(datetime.now(tz=timezone.utc).timestamp()),
        "exp": int((datetime.now(tz=timezone.utc) + timedelta(seconds=3600)).timestamp()),
    },
    "wrong-secret-used-to-sign-this-forged-token",
    algorithm="HS256",
)
MISSING_TOKEN = ""

__all__ = (
    "ALGORITHMS",
    "AUDIENCE",
    "EXPIRED_TOKEN",
    "FORGED_TOKEN",
    "ISSUER",
    "MISSING_TOKEN",
    "SECRET",
    "TOKEN_URL",
    "VALID_TOKEN",
    "AuthenticatedUser",
    "BearerTokenValidator",
    "bearer_token_validator",
    "build_mcp_auth_config",
    "build_oauth_backend",
    "mint_access_token",
)
