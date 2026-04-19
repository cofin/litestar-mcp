"""Unit tests for the :class:`JWKSCache` protocol and :class:`DefaultJWKSCache`.

Ch3 of ``v0.5.0-consumer-readiness`` introduces an injectable JWKS cache so
applications can share one document cache across their own auth stack and
litestar-mcp's OIDC validator.
"""

import asyncio
from typing import Any

import pytest

from litestar_mcp.auth import DefaultJWKSCache, JWKSCache

pytestmark = pytest.mark.unit


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _InMemoryJWKSCache:
    """Test double: counts operations so we can prove injection."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.gets = 0
        self.sets = 0
        self.invalidations = 0

    async def get(self, url: str) -> dict[str, Any] | None:
        self.gets += 1
        return self.store.get(url)

    async def set(self, url: str, document: dict[str, Any], *, ttl: int) -> None:
        self.sets += 1
        self.store[url] = document

    async def invalidate(self, url: str) -> None:
        self.invalidations += 1
        self.store.pop(url, None)


def test_custom_cache_satisfies_protocol() -> None:
    """Duck-typed implementations must pass the :class:`JWKSCache` runtime check."""
    cache: JWKSCache = _InMemoryJWKSCache()
    assert isinstance(cache, JWKSCache)


@pytest.mark.anyio
async def test_default_cache_roundtrip() -> None:
    cache = DefaultJWKSCache()
    await cache.set("https://example/x", {"keys": []}, ttl=10)
    assert (await cache.get("https://example/x")) == {"keys": []}


@pytest.mark.anyio
async def test_default_cache_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = DefaultJWKSCache()
    now = [100.0]
    monkeypatch.setattr("litestar_mcp.auth.oidc.monotonic", lambda: now[0])

    await cache.set("u", {"k": 1}, ttl=5)
    assert (await cache.get("u")) == {"k": 1}
    now[0] = 107.0
    assert (await cache.get("u")) is None


@pytest.mark.anyio
async def test_default_cache_invalidate() -> None:
    cache = DefaultJWKSCache()
    await cache.set("u", {"k": 1}, ttl=60)
    await cache.invalidate("u")
    assert (await cache.get("u")) is None


@pytest.mark.anyio
async def test_default_cache_clear() -> None:
    cache = DefaultJWKSCache()
    await cache.set("a", {"k": 1}, ttl=60)
    await cache.set("b", {"k": 2}, ttl=60)
    cache.clear()
    assert (await cache.get("a")) is None
    assert (await cache.get("b")) is None


@pytest.mark.anyio
async def test_default_cache_concurrent_writes_last_wins() -> None:
    """Concurrent writers don't corrupt the cache; last ``set`` wins."""
    cache = DefaultJWKSCache()

    async def writer(value: int) -> None:
        await cache.set("u", {"k": value}, ttl=60)

    await asyncio.gather(writer(1), writer(2))
    result = await cache.get("u")
    assert result in ({"k": 1}, {"k": 2})


@pytest.mark.anyio
async def test_get_default_cache_returns_singleton() -> None:
    from litestar_mcp.auth.oidc import get_default_cache

    assert get_default_cache() is get_default_cache()


@pytest.mark.anyio
async def test_reset_default_cache_clears_state() -> None:
    from litestar_mcp.auth.oidc import get_default_cache, reset_default_cache

    cache = get_default_cache()
    await cache.set("u", {"k": 1}, ttl=60)
    reset_default_cache()
    assert (await cache.get("u")) is None


@pytest.mark.anyio
async def test_injected_cache_consulted_before_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_validate_oidc_bearer`` reads from the injected cache before dispatching httpx."""
    import base64

    import jwt

    from litestar_mcp.auth.oidc import _validate_oidc_bearer

    secret = b"secret"
    kid = "test-key"
    jwks = {
        "keys": [
            {
                "kty": "oct",
                "k": base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii"),
                "kid": kid,
                "alg": "HS256",
            }
        ]
    }
    token = jwt.encode(
        {"sub": "alice", "iss": "https://issuer.test", "aud": "api"},
        secret,
        algorithm="HS256",
        headers={"kid": kid},
    )

    cache = _InMemoryJWKSCache()
    await cache.set("https://issuer.test/keys", jwks, ttl=60)

    async def fail_fetch(url: str) -> dict[str, Any]:
        msg = "Network fetch must not be called when cache hits"
        raise AssertionError(msg)

    monkeypatch.setattr("litestar_mcp.auth.oidc._fetch_json_document", fail_fetch)

    claims = await _validate_oidc_bearer(
        token,
        issuer="https://issuer.test",
        audience="api",
        jwks_uri="https://issuer.test/keys",
        algorithms=("HS256",),
        jwks_cache=cache,
    )

    assert claims is not None
    assert claims["sub"] == "alice"
    assert cache.gets >= 1
    assert cache.sets == 1  # only the pre-seed
