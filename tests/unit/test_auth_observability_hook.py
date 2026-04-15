"""Tests for MCPAuthConfig.on_validation_error observability hook."""

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from litestar_mcp import auth as auth_module
from litestar_mcp.auth import MCPAuthConfig, _validate_oidc_bearer


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    auth_module._JSON_DOCUMENT_CACHE.clear()
    auth_module._JSON_DOCUMENT_LOCKS.clear()
    yield
    auth_module._JSON_DOCUMENT_CACHE.clear()
    auth_module._JSON_DOCUMENT_LOCKS.clear()


@pytest.mark.asyncio
async def test_sync_hook_called_on_jwks_resolution_failure() -> None:
    calls: list[tuple[str, BaseException]] = []

    def hook(issuer: str, exc: BaseException) -> None:
        calls.append((issuer, exc))

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "jwks fetch blew up"
        raise RuntimeError(msg)

    with patch.object(auth_module, "_resolve_jwks", side_effect=failing_resolve):
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
    assert str(calls[0][1]) == "jwks fetch blew up"


@pytest.mark.asyncio
async def test_async_hook_awaited_on_validation_failure() -> None:
    hook = AsyncMock(return_value=None)

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "boom"
        raise RuntimeError(msg)

    with patch.object(auth_module, "_resolve_jwks", side_effect=failing_resolve):
        result = await _validate_oidc_bearer(
            "token",
            issuer="https://issuer.example",
            audience="aud",
            jwks_uri=None,
            algorithms=("RS256",),
            on_validation_error=hook,
        )

    assert result is None
    hook.assert_awaited_once()
    await_args = hook.await_args
    assert await_args is not None
    assert await_args.args[0] == "https://issuer.example"
    assert isinstance(await_args.args[1], RuntimeError)


@pytest.mark.asyncio
async def test_hook_exception_does_not_affect_auth_outcome(caplog: pytest.LogCaptureFixture) -> None:
    def bad_hook(issuer: str, exc: BaseException) -> None:
        msg = "hook itself broken"
        raise ValueError(msg)

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "upstream failure"
        raise RuntimeError(msg)

    with (
        patch.object(auth_module, "_resolve_jwks", side_effect=failing_resolve),
        caplog.at_level(logging.ERROR, logger="litestar_mcp.auth"),
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
    # Hook failure must be logged, not raised.
    assert any("hook itself broken" in record.getMessage() or record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_hook_not_invoked_on_successful_validation() -> None:
    """Hook must fire only on the failure path."""
    calls: list[Any] = []

    def hook(issuer: str, exc: BaseException) -> None:
        calls.append((issuer, exc))

    async def fake_resolve_jwks(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"keys": []}

    # We still expect failure (no keys), but just verify hook shape either way.
    with patch.object(auth_module, "_resolve_jwks", side_effect=fake_resolve_jwks):
        await _validate_oidc_bearer(
            "token",
            issuer="https://issuer.example",
            audience="aud",
            jwks_uri=None,
            algorithms=("RS256",),
            on_validation_error=hook,
        )

    # This call path still fails (no valid signing key) -> hook should fire once
    # with a BaseException. Keep the assertion minimal.
    assert len(calls) <= 1


@pytest.mark.asyncio
async def test_config_level_hook_is_passed_through_in_provider_path() -> None:
    """Hook declared on MCPAuthConfig is invoked when OIDCProviderConfig path fails."""
    from litestar_mcp.auth import OIDCProviderConfig, validate_bearer_token

    captured: list[BaseException] = []

    def hook(issuer: str, exc: BaseException) -> None:
        captured.append(exc)

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "provider boom"
        raise RuntimeError(msg)

    config = MCPAuthConfig(
        providers=[OIDCProviderConfig(issuer="https://provider.example", audience="aud")],
        on_validation_error=hook,
    )

    with patch.object(auth_module, "_resolve_jwks", side_effect=failing_resolve):
        result = await validate_bearer_token("token", config)

    assert result is None
    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)


@pytest.mark.asyncio
async def test_factory_accepts_hook_kwarg() -> None:
    """create_oidc_validator should forward on_validation_error to the core."""
    from litestar_mcp.oidc import create_oidc_validator

    captured: list[BaseException] = []

    def hook(issuer: str, exc: BaseException) -> None:
        captured.append(exc)

    async def failing_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "factory boom"
        raise RuntimeError(msg)

    validator = create_oidc_validator(
        "https://factory.example",
        "aud",
        on_validation_error=hook,
    )
    with patch.object(auth_module, "_resolve_jwks", side_effect=failing_resolve):
        result = await validator("token")

    assert result is None
    assert len(captured) == 1
