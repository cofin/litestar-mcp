"""Shared auth helpers for the reference notes examples."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Final, cast

import httpx
import jwt
import msgspec
from litestar import Controller, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult, DefineMiddleware
from litestar.security.jwt import OAuth2PasswordBearerAuth, Token
from litestar.types import ASGIApp

DEFAULT_ISSUER: "Final" = "http://localhost:8000/auth"
DEFAULT_AUDIENCE: "Final" = "http://localhost:8000/api"
DEFAULT_ALGORITHM: "Final" = "HS256"
DEFAULT_TOKEN_PATH: "Final" = "/auth/login"

DEFAULT_IAP_ISSUER: "Final" = "https://cloud.google.com/iap"
DEFAULT_IAP_JWKS_URL: "Final" = "https://www.gstatic.com/iap/verify/public_key-jwk"
DEFAULT_IAP_ALGORITHM: "Final" = "ES256"
IAP_HEADER_NAME: "Final" = "x-goog-iap-jwt-assertion"
IAP_JWKS_CACHE_TTL: "Final" = 3600


class AuthenticatedIdentity(msgspec.Struct, kw_only=True):
    """Validated identity shared across JWT and Google IAP examples."""

    sub: "str"
    email: "str | None" = None


class LoginInput(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """Login payload accepted by the demo ``/auth/login`` endpoint."""

    username: "str"
    password: "str"


class LoginResponse(msgspec.Struct, kw_only=True):
    """Bearer-token response returned by the demo login endpoint."""

    access_token: "str"
    token_type: "str" = "bearer"


def _normalize_email(value: "object") -> "str | None":
    if not isinstance(value, str) or not value:
        return None
    if ":" in value:
        _, suffix = value.split(":", 1)
        if "@" in suffix:
            return suffix
    return value


def identity_from_claims(claims: "dict[str, Any]") -> "AuthenticatedIdentity":
    """Map validated claims into the shared identity shape."""
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        msg = "Validated claims must include a non-empty 'sub' value"
        raise ValueError(msg)
    return AuthenticatedIdentity(sub=sub, email=_normalize_email(claims.get("email")))


def mint_hs256_token(
    subject: "str",
    *,
    secret: "str",
    issuer: "str" = DEFAULT_ISSUER,
    audience: "str" = DEFAULT_AUDIENCE,
    expires_in: "int" = 3600,
    extra_claims: "Mapping[str, Any] | None" = None,
) -> "str":
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
    *, secret: "str", issuer: "str" = DEFAULT_ISSUER, audience: "str" = DEFAULT_AUDIENCE
) -> "Callable[[str], Any]":
    """Build an async HS256 token validator."""

    async def _validate(token: "str") -> "dict[str, Any] | None":
        try:
            claims = jwt.decode(token, secret, algorithms=[DEFAULT_ALGORITHM], audience=audience, issuer=issuer)
        except jwt.PyJWTError:
            return None
        if not isinstance(claims, dict):  # pragma: no cover - defensive
            return None  # type: ignore[unreachable]
        return claims

    return _validate


async def _retrieve_identity_from_token(
    token: "Token", _connection: "ASGIConnection[Any, Any, Any, Any]"
) -> "AuthenticatedIdentity":
    extras = token.extras or {}
    return AuthenticatedIdentity(sub=token.sub, email=_normalize_email(extras.get("email")))


def build_oauth_backend(
    *,
    secret: "str",
    issuer: "str" = DEFAULT_ISSUER,
    audience: "str" = DEFAULT_AUDIENCE,
    token_url: "str" = DEFAULT_TOKEN_PATH,
    exclude: "list[str] | None" = None,
) -> "OAuth2PasswordBearerAuth[AuthenticatedIdentity, Token]":
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


def build_login_controller(
    *,
    user_directory: "Mapping[str, str]",
    token_signer: "Callable[[str], str]",
    path: "str" = DEFAULT_TOKEN_PATH,
) -> "type[Controller]":
    """Build the minimal ``/auth/login`` controller used by JWT variants."""
    directory: dict[str, str] = dict(user_directory)
    controller_path = path

    class LoginController(Controller):
        path = controller_path

        @post("/", sync_to_thread=False)
        def login(self, data: "LoginInput") -> "LoginResponse":
            expected = directory.get(data.username)
            if expected is None or expected != data.password:
                msg = "Invalid credentials"
                raise NotAuthorizedException(msg)
            return LoginResponse(access_token=token_signer(data.username))

    return LoginController


# ---------------------------------------------------------------------------
# Google IAP helpers
# ---------------------------------------------------------------------------

_JWKS_CACHE: "dict[str, tuple[float, dict[str, Any]]]" = {}


def seed_jwks_cache(url: "str", jwks: "dict[str, Any]", *, ttl: "int" = IAP_JWKS_CACHE_TTL) -> "None":
    """Pre-populate the IAP JWKS cache (used by tests to avoid network fetches)."""
    _JWKS_CACHE[url] = (monotonic() + ttl, jwks)


async def fetch_jwks(url: "str", *, ttl: "int" = IAP_JWKS_CACHE_TTL) -> "dict[str, Any]":
    """Return the JWK set at ``url``, cached in-process for ``ttl`` seconds."""
    cached = _JWKS_CACHE.get(url)
    if cached is not None and cached[0] > monotonic():
        return cached[1]
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        document = cast("dict[str, Any]", response.json())
    _JWKS_CACHE[url] = (monotonic() + ttl, document)
    return document


def _load_jwks_signing_key(token: "str", jwks: "dict[str, Any]") -> "Any":
    """Pick the JWKS entry matching the token's ``kid`` and return a key object."""
    from jwt import algorithms as jwt_algorithms
    from litestar.serialization import encode_json

    header = jwt.get_unverified_header(token)
    key_id = header.get("kid")
    for candidate in jwks.get("keys", []):
        if key_id is None or candidate.get("kid") == key_id:
            algorithm = jwt_algorithms.get_default_algorithms()[DEFAULT_IAP_ALGORITHM]
            return algorithm.from_jwk(encode_json(candidate).decode("utf-8"))
    msg = "No matching IAP signing key"
    raise ValueError(msg)


def build_iap_token_validator(
    *,
    audience: "str",
    issuer: "str" = DEFAULT_IAP_ISSUER,
    jwks_url: "str" = DEFAULT_IAP_JWKS_URL,
    leeway_seconds: "int" = 30,
) -> "Callable[[str], Awaitable[dict[str, Any] | None]]":
    """Build a validator for Google IAP signed assertions."""

    async def _validate(token: "str") -> "dict[str, Any] | None":
        try:
            jwks = await fetch_jwks(jwks_url)
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


class IAPAuthenticationMiddleware(AbstractAuthenticationMiddleware):
    """Validate the Google IAP assertion header and populate ``request.user`` / ``request.auth``."""

    def __init__(
        self,
        app: "ASGIApp",
        *,
        audience: "str",
        issuer: "str" = DEFAULT_IAP_ISSUER,
        jwks_url: "str" = DEFAULT_IAP_JWKS_URL,
        **kwargs: "Any",
    ) -> "None":
        super().__init__(app, **kwargs)
        self._validate = build_iap_token_validator(audience=audience, issuer=issuer, jwks_url=jwks_url)

    async def authenticate_request(self, connection: "ASGIConnection[Any, Any, Any, Any]") -> "AuthenticationResult":
        token = connection.headers.get(IAP_HEADER_NAME)
        if not token:
            msg = "Missing IAP assertion"
            raise NotAuthorizedException(msg)
        claims = await self._validate(token)
        if claims is None:
            msg = "Invalid IAP assertion"
            raise NotAuthorizedException(msg)
        return AuthenticationResult(user=identity_from_claims(claims), auth=claims)


def build_iap_auth_middleware(
    *, audience: "str", issuer: "str" = DEFAULT_IAP_ISSUER, jwks_url: "str" = DEFAULT_IAP_JWKS_URL
) -> "DefineMiddleware":
    """Build the IAP authentication middleware for the reference notes example."""
    return DefineMiddleware(IAPAuthenticationMiddleware, audience=audience, issuer=issuer, jwks_url=jwks_url)
