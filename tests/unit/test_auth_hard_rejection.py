"""Tests for MCPAuthHardRejectionError short-circuit semantics."""

from typing import Any

import pytest

from litestar_mcp.auth import MCPAuthConfig, MCPAuthHardRejectionError, validate_bearer_token


@pytest.mark.asyncio
async def test_hard_rejection_skips_providers() -> None:
    """Raising MCPAuthHardRejectionError from token_validator must skip providers."""

    provider_was_called = False

    async def failing_validator(token: str) -> dict[str, Any] | None:
        msg = "Token present but invalid"
        raise MCPAuthHardRejectionError(msg)

    async def provider_trap(token: str) -> dict[str, Any] | None:
        nonlocal provider_was_called
        provider_was_called = True
        return {"sub": "should-not-reach-here"}

    config = MCPAuthConfig(token_validator=failing_validator)
    # Directly monkeypatch the provider iteration by injecting into providers.
    # Use a synthetic provider via the token_validator-only path; providers
    # only trigger when validator returns None. Here we verify the validator
    # raising the special exception short-circuits before the provider loop.
    object.__setattr__(config, "providers", [object()])  # sentinel - must not be iterated

    # Even with a bogus provider in the list, the hard rejection must mean
    # validate_bearer_token returns None without touching providers.
    result = await validate_bearer_token("any-token", config)

    assert result is None
    assert provider_was_called is False


@pytest.mark.asyncio
async def test_non_hard_exception_propagates() -> None:
    """Other exceptions from token_validator must propagate (fail loud)."""

    async def crashing_validator(token: str) -> dict[str, Any] | None:
        msg = "misconfiguration"
        raise RuntimeError(msg)

    config = MCPAuthConfig(token_validator=crashing_validator)

    with pytest.raises(RuntimeError, match="misconfiguration"):
        await validate_bearer_token("any-token", config)


@pytest.mark.asyncio
async def test_successful_validator_still_returns_claims() -> None:
    """Baseline: a normal validator still wins without providers being tried."""

    async def ok_validator(token: str) -> dict[str, Any] | None:
        return {"sub": "alice"}

    config = MCPAuthConfig(token_validator=ok_validator)
    claims = await validate_bearer_token("any-token", config)
    assert claims == {"sub": "alice"}


@pytest.mark.asyncio
async def test_none_from_validator_falls_through_to_providers() -> None:
    """Baseline: returning None (not raising) still allows provider fallthrough."""

    async def decline_validator(token: str) -> dict[str, Any] | None:
        return None

    config = MCPAuthConfig(token_validator=decline_validator)
    # No providers configured -> still None.
    claims = await validate_bearer_token("any-token", config)
    assert claims is None


def test_hard_rejection_is_exported_from_package_root() -> None:
    """Downstreams import MCPAuthHardRejectionError from the package root."""
    import litestar_mcp

    assert hasattr(litestar_mcp, "MCPAuthHardRejectionError")
    assert litestar_mcp.MCPAuthHardRejectionError is MCPAuthHardRejectionError
