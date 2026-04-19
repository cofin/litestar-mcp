"""Unit tests for the public ``create_oidc_validator`` factory.

These tests use HS256 (symmetric) keys so we can synthesize JWKS documents
without needing real asymmetric keypairs. The factory must delegate to the
shared ``_validate_oidc_bearer`` core in :mod:`litestar_mcp.auth` so the
declarative ``OIDCProviderConfig`` path and the public factory share
behavior.
"""

import base64
import time
from typing import Any, cast
from unittest.mock import patch

import pytest

import litestar_mcp.auth._oidc as mcp_auth
from litestar_mcp import TokenValidator, create_oidc_validator
from litestar_mcp.auth._cache import reset_default_cache

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


ISSUER = "https://issuer.example.com"
AUDIENCE = "my-mcp-audience"
KID = "test-key"


def _hs256_jwk(secret: bytes, kid: str = KID) -> dict[str, Any]:
    return {
        "kty": "oct",
        "k": base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii"),
        "kid": kid,
        "alg": "HS256",
    }


def _encode(claims: dict[str, Any], secret: bytes, *, kid: str = KID) -> str:
    jwt = pytest.importorskip("jwt")
    return cast("str", jwt.encode(claims, secret, algorithm="HS256", headers={"kid": kid}))


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    reset_default_cache()
    mcp_auth._FETCH_LOCKS.clear()
    yield
    reset_default_cache()
    mcp_auth._FETCH_LOCKS.clear()


async def test_factory_returns_claims_for_valid_token() -> None:
    pytest.importorskip("jwt")
    secret = b"super-secret-key"
    jwks = {"keys": [_hs256_jwk(secret)]}

    validator: TokenValidator = create_oidc_validator(
        ISSUER,
        AUDIENCE,
        jwks_uri="https://issuer.example.com/keys",
        algorithms=("HS256",),
    )
    token = _encode({"sub": "alice", "iss": ISSUER, "aud": AUDIENCE}, secret)

    async def fake_fetch(url: str) -> dict[str, Any]:
        assert url == "https://issuer.example.com/keys"
        return jwks

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        claims = await validator(token)

    assert claims is not None
    assert claims["sub"] == "alice"


async def test_factory_rejects_wrong_signature() -> None:
    pytest.importorskip("jwt")
    real_secret = b"real-secret"
    rogue_secret = b"rogue-secret"
    jwks = {"keys": [_hs256_jwk(real_secret)]}

    validator = create_oidc_validator(
        ISSUER, AUDIENCE, jwks_uri="https://issuer.example.com/keys", algorithms=("HS256",)
    )
    token = _encode({"sub": "alice", "iss": ISSUER, "aud": AUDIENCE}, rogue_secret)

    async def fake_fetch(url: str) -> dict[str, Any]:
        return jwks

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        claims = await validator(token)

    assert claims is None


async def test_factory_rejects_wrong_issuer() -> None:
    pytest.importorskip("jwt")
    secret = b"secret"
    jwks = {"keys": [_hs256_jwk(secret)]}

    validator = create_oidc_validator(
        ISSUER, AUDIENCE, jwks_uri="https://issuer.example.com/keys", algorithms=("HS256",)
    )
    token = _encode({"sub": "alice", "iss": "https://attacker.example.com", "aud": AUDIENCE}, secret)

    async def fake_fetch(url: str) -> dict[str, Any]:
        return jwks

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        claims = await validator(token)

    assert claims is None


async def test_factory_rejects_wrong_audience() -> None:
    pytest.importorskip("jwt")
    secret = b"secret"
    jwks = {"keys": [_hs256_jwk(secret)]}

    validator = create_oidc_validator(
        ISSUER, AUDIENCE, jwks_uri="https://issuer.example.com/keys", algorithms=("HS256",)
    )
    token = _encode({"sub": "alice", "iss": ISSUER, "aud": "some-other-audience"}, secret)

    async def fake_fetch(url: str) -> dict[str, Any]:
        return jwks

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        claims = await validator(token)

    assert claims is None


async def test_factory_clock_skew_accepts_recently_expired() -> None:
    pytest.importorskip("jwt")
    secret = b"secret"
    jwks = {"keys": [_hs256_jwk(secret)]}

    validator = create_oidc_validator(
        ISSUER,
        AUDIENCE,
        jwks_uri="https://issuer.example.com/keys",
        algorithms=("HS256",),
        clock_skew=30,
    )
    now = int(time.time())
    # Expired 20 seconds ago — within 30s skew → must accept.
    token = _encode(
        {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE, "exp": now - 20, "iat": now - 120},
        secret,
    )

    async def fake_fetch(url: str) -> dict[str, Any]:
        return jwks

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        claims = await validator(token)

    assert claims is not None
    assert claims["sub"] == "alice"


async def test_factory_clock_skew_rejects_far_expired() -> None:
    pytest.importorskip("jwt")
    secret = b"secret"
    jwks = {"keys": [_hs256_jwk(secret)]}

    validator = create_oidc_validator(
        ISSUER,
        AUDIENCE,
        jwks_uri="https://issuer.example.com/keys",
        algorithms=("HS256",),
        clock_skew=30,
    )
    now = int(time.time())
    # Expired 60 seconds ago — beyond 30s skew → must reject.
    token = _encode(
        {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE, "exp": now - 60, "iat": now - 300},
        secret,
    )

    async def fake_fetch(url: str) -> dict[str, Any]:
        return jwks

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        claims = await validator(token)

    assert claims is None


async def test_factory_jwks_cache_ttl_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("jwt")
    secret = b"secret"
    jwks = {"keys": [_hs256_jwk(secret)]}

    fetch_count = 0

    async def fake_fetch(url: str) -> dict[str, Any]:
        nonlocal fetch_count
        fetch_count += 1
        return jwks

    current_time = [1000.0]

    def fake_monotonic() -> float:
        return current_time[0]

    monkeypatch.setattr("litestar_mcp.auth._cache.monotonic", fake_monotonic)

    validator = create_oidc_validator(
        ISSUER,
        AUDIENCE,
        jwks_uri="https://issuer.example.com/keys",
        algorithms=("HS256",),
        jwks_cache_ttl=60,
    )
    token = _encode({"sub": "alice", "iss": ISSUER, "aud": AUDIENCE}, secret)

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        # First call -> fetches once.
        assert await validator(token) is not None
        assert fetch_count == 1

        # Within TTL -> still 1.
        current_time[0] += 30
        assert await validator(token) is not None
        assert fetch_count == 1

        # After TTL -> fetches again.
        current_time[0] += 60
        assert await validator(token) is not None
        assert fetch_count == 2


async def test_factory_auto_discovery_when_no_jwks_uri() -> None:
    pytest.importorskip("jwt")
    secret = b"secret"
    jwks = {"keys": [_hs256_jwk(secret)]}

    async def fake_fetch(url: str) -> dict[str, Any]:
        if url.endswith("/.well-known/openid-configuration"):
            return {"jwks_uri": "https://issuer.example.com/keys"}
        if url == "https://issuer.example.com/keys":
            return jwks
        msg = f"unexpected url: {url}"
        raise AssertionError(msg)

    validator = create_oidc_validator(ISSUER, AUDIENCE, algorithms=("HS256",))
    token = _encode({"sub": "alice", "iss": ISSUER, "aud": AUDIENCE}, secret)

    with patch("litestar_mcp.auth._oidc._fetch_json_document", side_effect=fake_fetch):
        claims = await validator(token)

    assert claims is not None
