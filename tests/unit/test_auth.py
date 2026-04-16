"""Tests for the collapsed ``MCPAuthConfig`` metadata and its manifest.

Post-Ch3, ``MCPAuthConfig`` no longer carries enforcement fields — those live
on :class:`~litestar_mcp.auth.MCPAuthBackend`. The surviving fields describe
the auth surface advertised by ``/.well-known/oauth-protected-resource``.

Backend behavior: ``tests/unit/test_auth_backend.py``.
Middleware wiring + well-known exemption: ``tests/integration/test_mcp_auth_middleware.py``.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthConfig, OIDCProviderConfig
from litestar_mcp.decorators import mcp_tool


def _make_app(auth_config: MCPAuthConfig | None = None) -> Litestar:
    @get("/users", sync_to_thread=False)
    @mcp_tool(name="list_users")
    def list_users() -> list[dict[str, Any]]:
        """List users."""
        return [{"id": 1, "name": "Alice"}]

    mcp_config = MCPConfig(auth=auth_config)
    return Litestar(route_handlers=[list_users], plugins=[LitestarMCP(mcp_config)])


class TestMCPAuthConfigShape:
    """Ch3 collapses MCPAuthConfig to metadata: only issuer/audience/scopes survive."""

    def test_defaults_all_none(self) -> None:
        config = MCPAuthConfig()
        assert config.issuer is None
        assert config.audience is None
        assert config.scopes is None

    def test_field_set_matches_spec(self) -> None:
        """Guard against regression — the collapsed shape has exactly 3 fields."""
        names = {f.name for f in fields(MCPAuthConfig)}
        assert names == {"issuer", "audience", "scopes"}, names

    def test_populated_metadata(self) -> None:
        config = MCPAuthConfig(
            issuer="https://idp.example.com",
            audience="my-mcp-server",
            scopes={"mcp:read": "Read MCP tools", "mcp:write": "Write MCP tools"},
        )
        assert config.issuer == "https://idp.example.com"
        assert config.audience == "my-mcp-server"
        assert config.scopes == {"mcp:read": "Read MCP tools", "mcp:write": "Write MCP tools"}


class TestOIDCProviderConfig:
    """OIDCProviderConfig surface survives the collapse unchanged."""

    def test_minimal_construction(self) -> None:
        provider = OIDCProviderConfig(issuer="https://issuer.example.com")
        assert provider.issuer == "https://issuer.example.com"
        assert provider.audience is None
        assert provider.algorithms == ["RS256"]

    def test_full_construction(self) -> None:
        provider = OIDCProviderConfig(
            issuer="https://issuer.example.com",
            audience=["api-1", "api-2"],
            jwks_uri="https://issuer.example.com/jwks",
            algorithms=["RS256", "ES256"],
            cache_ttl=600,
            clock_skew=60,
        )
        assert provider.audience == ["api-1", "api-2"]
        assert provider.jwks_uri == "https://issuer.example.com/jwks"
        assert provider.algorithms == ["RS256", "ES256"]
        assert provider.cache_ttl == 600
        assert provider.clock_skew == 60


class TestProtectedResourceMetadata:
    """The collapsed config still drives /.well-known/oauth-protected-resource correctly."""

    def test_well_known_with_explicit_auth_config(self) -> None:
        auth = MCPAuthConfig(issuer="https://auth.example.com", audience="my-mcp-server")
        with TestClient(app=_make_app(auth_config=auth)) as client:
            resp = client.get("/.well-known/oauth-protected-resource")
            assert resp.status_code == 200
            data = resp.json()
            assert data["resource"] == "my-mcp-server"
            assert data["authorization_servers"] == ["https://auth.example.com"]

    def test_well_known_with_scopes(self) -> None:
        auth = MCPAuthConfig(
            issuer="https://auth.example.com",
            audience="mcp",
            scopes={"mcp:read": "Read", "mcp:write": "Write"},
        )
        with TestClient(app=_make_app(auth_config=auth)) as client:
            resp = client.get("/.well-known/oauth-protected-resource")
            assert resp.status_code == 200
            data = resp.json()
            assert set(data["scopes_supported"]) == {"mcp:read", "mcp:write"}

    def test_well_known_no_auth_returns_empty_authorization_servers(self) -> None:
        with TestClient(app=_make_app()) as client:
            resp = client.get("/.well-known/oauth-protected-resource")
            assert resp.status_code == 200
            assert resp.json()["authorization_servers"] == []
