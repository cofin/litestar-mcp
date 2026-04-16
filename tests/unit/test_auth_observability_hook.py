"""Tests for the ``on_validation_error`` hook passed through to _validate_oidc_bearer.

Post-Ch3 the observability hook is no longer a field on ``MCPAuthConfig`` —
callers wire it directly into :func:`~litestar_mcp.auth.create_oidc_validator`
or pass it through the internal ``_validate_oidc_bearer`` call path. These
tests cover the core hook mechanics.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from litestar_mcp.auth import _oidc as oidc_internals
from litestar_mcp.auth._oidc import _validate_oidc_bearer


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    oidc_internals._JSON_DOCUMENT_CACHE.clear()
    oidc_internals._JSON_DOCUMENT_LOCKS.clear()
    yield
    oidc_internals._JSON_DOCUMENT_CACHE.clear()
    oidc_internals._JSON_DOCUMENT_LOCKS.clear()


@pytest.mark.asyncio
async def test_sync_hook_called_on_jwks_resolution_failure() -> None:
    calls: list[tuple[str, BaseException]] = []

    def hook(issuer: str, exc: BaseException) -> None:
        calls.append((issuer, exc))

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "jwks fetch blew up"
        raise RuntimeError(msg)

    with patch.object(oidc_internals, "_resolve_jwks", side_effect=failing_resolve):
        result = await _validate_oidc_bearer(
            "token",
            issuer="https://issuer.example",
            audience="aud",
            jwks_uri=None,
            algorithms=("RS256",),
            on_validation_error=hook,
        )

    assert result is None
    assert len(calls) == 1
    assert calls[0][0] == "https://issuer.example"
    assert isinstance(calls[0][1], RuntimeError)


@pytest.mark.asyncio
async def test_async_hook_is_awaited() -> None:
    captured: list[BaseException] = []

    async def hook(issuer: str, exc: BaseException) -> None:
        captured.append(exc)

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "async hook path"
        raise RuntimeError(msg)

    with patch.object(oidc_internals, "_resolve_jwks", side_effect=failing_resolve):
        result = await _validate_oidc_bearer(
            "token",
            issuer="https://issuer.example",
            audience="aud",
            jwks_uri=None,
            algorithms=("RS256",),
            on_validation_error=hook,
        )

    assert result is None
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_hook_exceptions_are_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    """A raise inside the hook must not change the auth outcome."""

    def bad_hook(issuer: str, exc: BaseException) -> None:
        msg = "observability plumbing exploded"
        raise RuntimeError(msg)

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "underlying failure"
        raise RuntimeError(msg)

    with (
        caplog.at_level(logging.ERROR, logger="litestar_mcp.auth._oidc"),
        patch.object(oidc_internals, "_resolve_jwks", side_effect=failing_resolve),
    ):
        result = await _validate_oidc_bearer(
            "token",
            issuer="https://issuer.example",
            audience="aud",
            jwks_uri=None,
            algorithms=("RS256",),
            on_validation_error=bad_hook,
        )

    assert result is None
    assert any("on_validation_error hook raised" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_factory_forwards_hook() -> None:
    """create_oidc_validator forwards on_validation_error through to the core."""
    from litestar_mcp.auth import create_oidc_validator

    captured: list[BaseException] = []

    def hook(issuer: str, exc: BaseException) -> None:
        captured.append(exc)

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "factory boom"
        raise RuntimeError(msg)

    validator = create_oidc_validator("https://factory.example", "aud", on_validation_error=hook)
    with patch.object(oidc_internals, "_resolve_jwks", side_effect=failing_resolve):
        result = await validator("token")

    assert result is None
    assert len(captured) == 1
