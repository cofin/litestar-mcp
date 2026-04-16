"""Unit-level sanity checks for the shared integration bearer-auth helpers."""

import pytest

from tests.integration._auth import (
    AUDIENCE,
    EXPIRED_TOKEN,
    FORGED_TOKEN,
    ISSUER,
    VALID_TOKEN,
    BearerTokenValidator,
    bearer_token_validator,
    build_mcp_auth_config,
    build_oauth_backend,
    mint_access_token,
)


@pytest.mark.asyncio
async def test_valid_token_round_trips_through_validator() -> None:
    claims = await bearer_token_validator(VALID_TOKEN)

    assert claims is not None
    assert claims["sub"] == "integration-user"
    assert claims["iss"] == ISSUER
    assert claims["aud"] == AUDIENCE
    assert "mcp:read" in claims["scopes"]


@pytest.mark.asyncio
async def test_forged_token_fails_validation() -> None:
    assert await bearer_token_validator(FORGED_TOKEN) is None


@pytest.mark.asyncio
async def test_expired_token_fails_validation() -> None:
    assert await bearer_token_validator(EXPIRED_TOKEN) is None


@pytest.mark.asyncio
async def test_bearer_token_validator_class_matches_function() -> None:
    validator = BearerTokenValidator()

    assert await validator(VALID_TOKEN) is not None
    assert await validator(FORGED_TOKEN) is None


@pytest.mark.asyncio
async def test_mint_access_token_custom_subject_is_propagated() -> None:
    token = mint_access_token(subject="alice", scopes=["mcp:read"])

    claims = await bearer_token_validator(token)

    assert claims is not None
    assert claims["sub"] == "alice"
    assert claims["scopes"] == ["mcp:read"]


def test_build_oauth_backend_excludes_mcp_and_well_known_paths() -> None:
    backend = build_oauth_backend()

    exclude = backend.exclude or []
    assert any("mcp" in rule for rule in exclude)
    assert any("well-known" in rule for rule in exclude)


def test_build_mcp_auth_config_exposes_metadata() -> None:
    """Ch3: MCPAuthConfig is pure metadata; enforcement is in the separate middleware helper."""
    from tests.integration._auth import build_mcp_auth_middleware

    config = build_mcp_auth_config()
    assert config.issuer == ISSUER
    assert config.audience == AUDIENCE

    middleware = build_mcp_auth_middleware()
    assert middleware is not None
