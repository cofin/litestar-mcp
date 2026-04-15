"""Shared auth helpers for the reference notes examples."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import jwt
import msgspec
from litestar import Controller, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.security.jwt import OAuth2PasswordBearerAuth, Token

from litestar_mcp.auth import MCPAuthConfig

DEFAULT_ISSUER: Final = "http://localhost:8000/auth"
DEFAULT_AUDIENCE: Final = "http://localhost:8000/api"
DEFAULT_ALGORITHM: Final = "HS256"
DEFAULT_TOKEN_PATH: Final = "/auth/login"


class AuthenticatedIdentity(msgspec.Struct, kw_only=True):
    """Validated identity shared across JWT and Google IAP examples."""

    sub: str
    email: str | None = None


class LoginInput(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """Login payload accepted by the demo ``/auth/login`` endpoint."""

    username: str
    password: str


class LoginResponse(msgspec.Struct, kw_only=True):
    """Bearer-token response returned by the demo login endpoint."""

    access_token: str
    token_type: str = "bearer"


def _normalize_email(value: object) -> str | None:
    """Normalize provider-prefixed email values into plain email addresses."""
    if not isinstance(value, str) or not value:
        return None

    if ":" in value:
        _, suffix = value.split(":", 1)
        if "@" in suffix:
            return suffix

    return value


def identity_from_claims(claims: dict[str, Any]) -> AuthenticatedIdentity:
    """Map validated claims into the shared identity shape."""
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        msg = "Validated claims must include a non-empty 'sub' value"
        raise ValueError(msg)

    return AuthenticatedIdentity(
        sub=sub,
        email=_normalize_email(claims.get("email")),
    )


def mint_hs256_token(
    subject: str,
    *,
    secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    expires_in: int = 3600,
    extra_claims: Mapping[str, Any] | None = None,
) -> str:
    """Mint an HS256-signed bearer token with the shared claim set.

    This helper exists primarily so the reference examples and their
    tests do not drift on claim conventions.
    """
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    if extra_claims:
        payload.update(dict(extra_claims))
    return jwt.encode(payload, secret, algorithm=DEFAULT_ALGORITHM)


def build_token_validator(
    *, secret: str, issuer: str = DEFAULT_ISSUER, audience: str = DEFAULT_AUDIENCE
) -> Callable[[str], Any]:
    """Build an async HS256 token validator used by both MCP and app auth."""

    async def _validate(token: str) -> dict[str, Any] | None:
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=[DEFAULT_ALGORITHM],
                audience=audience,
                issuer=issuer,
            )
        except jwt.PyJWTError:
            return None
        if not isinstance(claims, dict):  # pragma: no cover - defensive
            return None  # type: ignore[unreachable]
        return claims

    return _validate


async def _retrieve_identity_from_token(
    token: Token, _connection: ASGIConnection[Any, Any, Any, Any]
) -> AuthenticatedIdentity:
    """Resolve a validated Litestar :class:`Token` into an identity."""
    extras = token.extras or {}
    return AuthenticatedIdentity(sub=token.sub, email=_normalize_email(extras.get("email")))


def build_oauth_backend(
    *,
    secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    token_url: str = DEFAULT_TOKEN_PATH,
    exclude: list[str] | None = None,
) -> OAuth2PasswordBearerAuth[AuthenticatedIdentity, Token]:
    """Build the Litestar OAuth2 bearer backend for the reference notes examples.

    The MCP plugin exposes its own auth middleware for ``/mcp/*`` and the
    ``/.well-known/`` discovery routes, so the OAuth2 backend excludes those
    paths and leaves the app auth backend in charge of the rest of the app.
    """
    backend: OAuth2PasswordBearerAuth[AuthenticatedIdentity, Token] = OAuth2PasswordBearerAuth[
        AuthenticatedIdentity, Token
    ](
        token_secret=secret,
        token_url=token_url,
        retrieve_user_handler=_retrieve_identity_from_token,
        algorithm=DEFAULT_ALGORITHM,
        exclude=exclude or ["^/mcp(/.*)?$", "^/.well-known/"],
        accepted_issuers=[issuer],
        accepted_audiences=[audience],
    )
    return backend


async def _mcp_user_resolver(claims: dict[str, Any], _app: Any) -> AuthenticatedIdentity:
    """Resolve MCP-validated claims into :class:`AuthenticatedIdentity`."""
    return identity_from_claims(claims)


def build_mcp_auth_config(
    *, secret: str, issuer: str = DEFAULT_ISSUER, audience: str = DEFAULT_AUDIENCE
) -> MCPAuthConfig:
    """Build the MCP-side auth config paired with :func:`build_oauth_backend`."""
    return MCPAuthConfig(
        issuer=issuer,
        audience=audience,
        token_validator=build_token_validator(secret=secret, issuer=issuer, audience=audience),
        user_resolver=_mcp_user_resolver,
    )


def build_login_controller(
    *,
    user_directory: Mapping[str, str],
    token_signer: Callable[[str], str],
    path: str = DEFAULT_TOKEN_PATH,
) -> type[Controller]:
    """Build the minimal ``/auth/login`` controller used by JWT variants.

    Args:
        user_directory: Mapping of ``sub`` -> password. Demo only; real apps
            MUST replace this with a real user store.
        token_signer: Callable that turns a ``sub`` into a signed JWT string.
        path: Path for the controller. Defaults to ``/auth/login``.

    Returns:
        A :class:`Controller` subclass exposing a single ``POST`` endpoint
        that returns :class:`LoginResponse`.
    """
    directory: dict[str, str] = dict(user_directory)
    controller_path = path

    class LoginController(Controller):
        """Demo-only login controller for the reference notes examples."""

        path = controller_path

        @post("/", sync_to_thread=False)
        def login(self, data: LoginInput) -> LoginResponse:
            expected = directory.get(data.username)
            if expected is None or expected != data.password:
                msg = "Invalid credentials"
                raise NotAuthorizedException(msg)
            return LoginResponse(access_token=token_signer(data.username))

    return LoginController
