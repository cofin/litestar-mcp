"""Shared auth helpers for the reference notes examples."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import jwt
import msgspec
from litestar import Controller, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.middleware import DefineMiddleware
from litestar.security.jwt import OAuth2PasswordBearerAuth, Token
from litestar.types import ASGIApp, Receive, Scope, Send

from litestar_mcp.auth import MCPAuthBackend, MCPAuthConfig
from litestar_mcp.auth.oidc import _get_cached_json_document

DEFAULT_ISSUER: Final = "http://localhost:8000/auth"
DEFAULT_AUDIENCE: Final = "http://localhost:8000/api"
DEFAULT_ALGORITHM: Final = "HS256"
DEFAULT_TOKEN_PATH: Final = "/auth/login"

DEFAULT_IAP_ISSUER: Final = "https://cloud.google.com/iap"
DEFAULT_IAP_JWKS_URL: Final = "https://www.gstatic.com/iap/verify/public_key-jwk"
DEFAULT_IAP_ALGORITHM: Final = "ES256"
IAP_HEADER_NAME: Final = "x-goog-iap-jwt-assertion"
IAP_JWKS_CACHE_TTL: Final = 3600


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
    return AuthenticatedIdentity(sub=sub, email=_normalize_email(claims.get("email")))


def mint_hs256_token(
    subject: str,
    *,
    secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    expires_in: int = 3600,
    extra_claims: Mapping[str, Any] | None = None,
) -> str:
    """Mint an HS256-signed bearer token with the shared claim set."""
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
    """Build an async HS256 token validator."""

    async def _validate(token: str) -> dict[str, Any] | None:
        try:
            claims = jwt.decode(token, secret, algorithms=[DEFAULT_ALGORITHM], audience=audience, issuer=issuer)
        except jwt.PyJWTError:
            return None
        if not isinstance(claims, dict):  # pragma: no cover - defensive
            return None  # type: ignore[unreachable]
        return claims

    return _validate


async def _retrieve_identity_from_token(
    token: Token, _connection: ASGIConnection[Any, Any, Any, Any]
) -> AuthenticatedIdentity:
    extras = token.extras or {}
    return AuthenticatedIdentity(sub=token.sub, email=_normalize_email(extras.get("email")))


async def _mcp_user_resolver(claims: dict[str, Any], _app: Any) -> AuthenticatedIdentity:
    """Resolve MCP-validated claims into :class:`AuthenticatedIdentity`."""
    return identity_from_claims(claims)


def build_oauth_backend(
    *,
    secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    token_url: str = DEFAULT_TOKEN_PATH,
    exclude: list[str] | None = None,
) -> OAuth2PasswordBearerAuth[AuthenticatedIdentity, Token]:
    """Build the Litestar OAuth2 bearer backend for the reference notes examples."""
    return OAuth2PasswordBearerAuth[AuthenticatedIdentity, Token](
        token_secret=secret,
        token_url=token_url,
        retrieve_user_handler=_retrieve_identity_from_token,
        algorithm=DEFAULT_ALGORITHM,
        exclude=exclude or ["^/.well-known/"],
        accepted_issuers=[issuer],
        accepted_audiences=[audience],
    )


def build_mcp_auth_metadata(*, issuer: str = DEFAULT_ISSUER, audience: str = DEFAULT_AUDIENCE) -> MCPAuthConfig:
    """Build the metadata-only auth config for /.well-known/oauth-protected-resource."""
    return MCPAuthConfig(issuer=issuer, audience=audience)


def build_mcp_auth_middleware(
    *, secret: str, issuer: str = DEFAULT_ISSUER, audience: str = DEFAULT_AUDIENCE
) -> DefineMiddleware:
    """Build a ``DefineMiddleware(MCPAuthBackend, ...)`` for HS256 token validation."""
    return DefineMiddleware(
        MCPAuthBackend,
        token_validator=build_token_validator(secret=secret, issuer=issuer, audience=audience),
        user_resolver=_mcp_user_resolver,
    )


def build_login_controller(
    *,
    user_directory: Mapping[str, str],
    token_signer: Callable[[str], str],
    path: str = DEFAULT_TOKEN_PATH,
) -> type[Controller]:
    """Build the minimal ``/auth/login`` controller used by JWT variants."""
    directory: dict[str, str] = dict(user_directory)
    controller_path = path

    class LoginController(Controller):
        path = controller_path

        @post("/", sync_to_thread=False)
        def login(self, data: LoginInput) -> LoginResponse:
            expected = directory.get(data.username)
            if expected is None or expected != data.password:
                msg = "Invalid credentials"
                raise NotAuthorizedException(msg)
            return LoginResponse(access_token=token_signer(data.username))

    return LoginController


# ---------------------------------------------------------------------------
# Google IAP helpers
# ---------------------------------------------------------------------------


def _load_jwks_signing_key(token: str, jwks: dict[str, Any]) -> Any:
    """Pick the JWKS entry matching the token's ``kid`` and return a key object."""
    import json as _json

    from jwt import algorithms as jwt_algorithms

    header = jwt.get_unverified_header(token)
    key_id = header.get("kid")
    for candidate in jwks.get("keys", []):
        if key_id is None or candidate.get("kid") == key_id:
            algorithm = jwt_algorithms.get_default_algorithms()[DEFAULT_IAP_ALGORITHM]
            return algorithm.from_jwk(_json.dumps(candidate))
    msg = "No matching IAP signing key"
    raise ValueError(msg)


def build_iap_token_validator(
    *,
    audience: str,
    issuer: str = DEFAULT_IAP_ISSUER,
    jwks_url: str = DEFAULT_IAP_JWKS_URL,
    leeway_seconds: int = 30,
) -> Callable[[str], Awaitable[dict[str, Any] | None]]:
    """Build a validator for Google IAP signed assertions."""

    async def _validate(token: str) -> dict[str, Any] | None:
        try:
            jwks = await _get_cached_json_document(jwks_url, IAP_JWKS_CACHE_TTL)
            signing_key = _load_jwks_signing_key(token, jwks)
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[DEFAULT_IAP_ALGORITHM],
                audience=audience,
                issuer=issuer,
                leeway=leeway_seconds,
            )
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(claims, dict):  # pragma: no cover - defensive
            return None  # type: ignore[unreachable]
        return claims

    return _validate


def build_iap_auth_middleware(
    *,
    audience: str,
    issuer: str = DEFAULT_IAP_ISSUER,
    jwks_url: str = DEFAULT_IAP_JWKS_URL,
) -> DefineMiddleware:
    """Build a ``DefineMiddleware(MCPAuthBackend, ...)`` for IAP token validation."""
    return DefineMiddleware(
        MCPAuthBackend,
        token_validator=build_iap_token_validator(audience=audience, issuer=issuer, jwks_url=jwks_url),
        user_resolver=_mcp_user_resolver,
    )


def build_iap_header_alias_middleware(app: ASGIApp) -> ASGIApp:
    """Alias ``x-goog-iap-jwt-assertion`` as ``Authorization: Bearer`` for downstream middleware."""

    async def _middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])
        iap_value: bytes | None = None
        has_authorization = False
        for name, value in headers:
            lower = name.lower()
            if lower == IAP_HEADER_NAME.encode("ascii"):
                iap_value = value
            elif lower == b"authorization":
                has_authorization = True

        if iap_value is not None and not has_authorization:
            headers.append((b"authorization", b"Bearer " + iap_value))
            new_scope = dict(scope)
            new_scope["headers"] = headers
            await app(new_scope, receive, send)
            return

        await app(scope, receive, send)

    return _middleware
