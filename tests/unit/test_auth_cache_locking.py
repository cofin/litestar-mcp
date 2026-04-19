"""Tests for single-flight locking on the JWKS/discovery document cache."""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from litestar_mcp.auth import _oidc as auth_module
from litestar_mcp.auth._cache import reset_default_cache


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    """Clear the default JWKS cache and fetch locks between tests."""
    reset_default_cache()
    auth_module._FETCH_LOCKS.clear()
    yield
    reset_default_cache()
    auth_module._FETCH_LOCKS.clear()


@pytest.mark.asyncio
async def test_concurrent_cold_fetch_single_flight() -> None:
    """N concurrent requests to the same URL must trigger exactly one fetch."""

    call_count = 0

    async def fake_fetch(url: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return {"url": url, "keys": []}

    with patch.object(auth_module, "_fetch_json_document", side_effect=fake_fetch):
        results = await asyncio.gather(
            *(auth_module._get_cached_json_document("https://issuer.example/jwks", 3600) for _ in range(20))
        )

    assert call_count == 1, f"expected single-flight, got {call_count} fetches"
    assert all(r == {"url": "https://issuer.example/jwks", "keys": []} for r in results)


@pytest.mark.asyncio
async def test_concurrent_distinct_urls_run_in_parallel() -> None:
    """Fetches to distinct URLs must not serialize on the lock."""

    fetch_delay = 0.1

    async def slow_fetch(url: str) -> dict[str, Any]:
        await asyncio.sleep(fetch_delay)
        return {"url": url}

    with patch.object(auth_module, "_fetch_json_document", side_effect=slow_fetch):
        start = time.monotonic()
        await asyncio.gather(
            auth_module._get_cached_json_document("https://a.example/jwks", 3600),
            auth_module._get_cached_json_document("https://b.example/jwks", 3600),
        )
        elapsed = time.monotonic() - start

    # Serial would be ~2 * fetch_delay. Parallel must complete within 1.5x.
    assert elapsed < fetch_delay * 1.5, f"expected parallel fetches, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_cache_hit_does_not_acquire_lock() -> None:
    """Warm cache reads must not block on the lock (common hot path)."""

    fetch_mock = AsyncMock(return_value={"cached": True})
    with patch.object(auth_module, "_fetch_json_document", fetch_mock):
        first = await auth_module._get_cached_json_document("https://warm.example/jwks", 3600)
        assert fetch_mock.await_count == 1

        # 50 warm reads; counter must not advance.
        results = await asyncio.gather(
            *(auth_module._get_cached_json_document("https://warm.example/jwks", 3600) for _ in range(50))
        )
        assert fetch_mock.await_count == 1
        assert all(r == {"cached": True} for r in results)
        assert first == {"cached": True}


@pytest.mark.asyncio
async def test_expired_cache_entry_triggers_single_refetch() -> None:
    """When the entry is expired, concurrent callers must still single-flight."""

    fetch_counter = 0

    async def counting_fetch(url: str) -> dict[str, Any]:
        nonlocal fetch_counter
        fetch_counter += 1
        await asyncio.sleep(0.02)
        return {"fetch": fetch_counter}

    # Seed an already-expired entry.
    url = "https://expired.example/jwks"
    from litestar_mcp.auth._cache import get_default_cache

    cache = get_default_cache()
    # Inject an expired entry by writing straight to the internal store — the
    # test expressly asserts that an expired entry behaves like a cache miss.
    cache._store[url] = (0.0, {"fetch": 0})

    with patch.object(auth_module, "_fetch_json_document", side_effect=counting_fetch):
        await asyncio.gather(*(auth_module._get_cached_json_document(url, 3600) for _ in range(10)))

    assert fetch_counter == 1
