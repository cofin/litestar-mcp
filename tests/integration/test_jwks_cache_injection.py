"""Shared-cache integration tests for injected :class:`JWKSCache`.

Proves that two OIDC validators sharing one :class:`JWKSCache` issue exactly
one JWKS fetch across multiple validations, while separate cache instances
isolate their fetches.
"""

import base64
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import jwt
import pytest

from litestar_mcp.auth import DefaultJWKSCache, create_oidc_validator
from litestar_mcp.auth.oidc import reset_default_cache

pytestmark = pytest.mark.integration

ISSUER = "https://shared-cache.test"
JWKS_URL = f"{ISSUER}/jwks"
AUDIENCE_A = "app-a"
AUDIENCE_B = "app-b"
SECRET = b"shared-cache-secret-value-123456"
KID = "test-key"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _jwks() -> dict[str, Any]:
    return {
        "keys": [
            {
                "kty": "oct",
                "k": base64.urlsafe_b64encode(SECRET).rstrip(b"=").decode("ascii"),
                "kid": KID,
                "alg": "HS256",
            }
        ]
    }


def _token(audience: str) -> str:
    return jwt.encode(
        {"sub": "alice", "iss": ISSUER, "aud": audience},
        SECRET,
        algorithm="HS256",
        headers={"kid": KID},
    )


@pytest.fixture(autouse=True)
def _reset_default_cache_fixture() -> Iterator[None]:
    reset_default_cache()
    from litestar_mcp.auth import oidc as oidc_internals

    oidc_internals._FETCH_LOCKS.clear()
    yield
    reset_default_cache()
    oidc_internals._FETCH_LOCKS.clear()


@pytest.mark.anyio
async def test_shared_cache_deduplicates_jwks_fetches() -> None:
    """One shared :class:`JWKSCache` → exactly one fetch for two validators."""
    shared = DefaultJWKSCache()
    fetch_count = 0

    async def fake_fetch(url: str) -> dict[str, Any]:
        nonlocal fetch_count
        assert url == JWKS_URL
        fetch_count += 1
        return _jwks()

    with patch("litestar_mcp.auth.oidc._fetch_json_document", side_effect=fake_fetch):
        validator_a = create_oidc_validator(
            ISSUER, AUDIENCE_A, jwks_uri=JWKS_URL, algorithms=("HS256",), jwks_cache=shared
        )
        validator_b = create_oidc_validator(
            ISSUER, AUDIENCE_B, jwks_uri=JWKS_URL, algorithms=("HS256",), jwks_cache=shared
        )

        claims_a = await validator_a(_token(AUDIENCE_A))
        claims_b = await validator_b(_token(AUDIENCE_B))

    assert claims_a is not None
    assert claims_b is not None
    assert fetch_count == 1


@pytest.mark.anyio
async def test_default_cache_deduplicates_jwks_fetches() -> None:
    """No explicit cache → process-wide default still dedupes across validators."""
    fetch_count = 0

    async def fake_fetch(url: str) -> dict[str, Any]:
        nonlocal fetch_count
        fetch_count += 1
        return _jwks()

    with patch("litestar_mcp.auth.oidc._fetch_json_document", side_effect=fake_fetch):
        validator_a = create_oidc_validator(ISSUER, AUDIENCE_A, jwks_uri=JWKS_URL, algorithms=("HS256",))
        validator_b = create_oidc_validator(ISSUER, AUDIENCE_B, jwks_uri=JWKS_URL, algorithms=("HS256",))
        claims_a = await validator_a(_token(AUDIENCE_A))
        claims_b = await validator_b(_token(AUDIENCE_B))

    assert claims_a is not None
    assert claims_b is not None
    assert fetch_count == 1


@pytest.mark.anyio
async def test_separate_caches_isolate_fetches() -> None:
    """Two separate :class:`DefaultJWKSCache` instances → two independent fetches."""
    cache_a = DefaultJWKSCache()
    cache_b = DefaultJWKSCache()
    fetch_count = 0

    async def fake_fetch(url: str) -> dict[str, Any]:
        nonlocal fetch_count
        fetch_count += 1
        return _jwks()

    with patch("litestar_mcp.auth.oidc._fetch_json_document", side_effect=fake_fetch):
        validator_a = create_oidc_validator(
            ISSUER, AUDIENCE_A, jwks_uri=JWKS_URL, algorithms=("HS256",), jwks_cache=cache_a
        )
        validator_b = create_oidc_validator(
            ISSUER, AUDIENCE_B, jwks_uri=JWKS_URL, algorithms=("HS256",), jwks_cache=cache_b
        )
        claims_a = await validator_a(_token(AUDIENCE_A))
        claims_b = await validator_b(_token(AUDIENCE_B))

    assert claims_a is not None
    assert claims_b is not None
    assert fetch_count == 2
