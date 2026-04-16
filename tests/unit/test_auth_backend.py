"""Unit tests for MCPAuthBackend (Ch3).

MCPAuthBackend is an :class:`litestar.middleware.authentication.AbstractAuthenticationMiddleware`
subclass that replaces the bespoke ``_authenticate_request`` path inside
``routes.py``. These tests drive it directly by calling
``authenticate_request(connection)`` — integration coverage of the middleware
install lives in ``tests/integration/test_mcp_auth_middleware.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException

from litestar_mcp.auth import MCPAuthBackend, OIDCProviderConfig
from litestar_mcp.auth import backend as backend_mod


async def _noop_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover - stub
    return None


def _connection(headers: dict[str, str] | None = None) -> ASGIConnection[Any, Any, Any, Any]:
    """Build a minimal ASGIConnection carrying the given HTTP headers."""
    header_list = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": header_list,
        "app": None,
        "litestar_app": object(),
        "router": None,
        "route_handler": None,
        "state": {},
    }
    return ASGIConnection(scope)  # type: ignore[arg-type]


def _make_backend(**kwargs: Any) -> MCPAuthBackend:
    return MCPAuthBackend(app=_noop_app, **kwargs)


class TestMCPAuthBackend:
    """Direct unit tests for MCPAuthBackend.authenticate_request."""

    @pytest.mark.asyncio
    async def test_validates_via_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A valid token routed through a single OIDCProviderConfig succeeds."""

        async def _fake_provider_validator(
            token: str, provider: OIDCProviderConfig, *, on_validation_error: Any = None
        ) -> dict[str, Any] | None:
            if token == "good-token":
                return {"sub": "alice", "iss": provider.issuer}
            return None

        monkeypatch.setattr(backend_mod, "_validate_with_oidc_provider", _fake_provider_validator)

        provider = OIDCProviderConfig(issuer="https://issuer.example.com", audience="aud")
        backend = _make_backend(providers=[provider])

        result = await backend.authenticate_request(_connection({"authorization": "Bearer good-token"}))

        assert result.user is None
        assert result.auth == {"sub": "alice", "iss": "https://issuer.example.com"}

    @pytest.mark.asyncio
    async def test_token_validator_tried_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both validator and providers are set, validator wins for tokens it accepts."""
        calls: dict[str, int] = {"validator": 0, "provider": 0}

        async def _validator(token: str) -> dict[str, Any] | None:
            calls["validator"] += 1
            return {"sub": "bob"}

        async def _provider_validator(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
            calls["provider"] += 1
            return {"sub": "charlie"}

        monkeypatch.setattr(backend_mod, "_validate_with_oidc_provider", _provider_validator)

        backend = _make_backend(
            providers=[OIDCProviderConfig(issuer="https://x", audience="y")],
            token_validator=_validator,
        )
        result = await backend.authenticate_request(_connection({"authorization": "Bearer anything"}))

        assert result.auth == {"sub": "bob"}
        assert calls == {"validator": 1, "provider": 0}

    @pytest.mark.asyncio
    async def test_falls_through_to_providers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When token_validator returns None, providers are tried next."""

        async def _declining_validator(token: str) -> dict[str, Any] | None:
            return None

        async def _succeeding_provider(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
            return {"sub": "via-provider"}

        monkeypatch.setattr(backend_mod, "_validate_with_oidc_provider", _succeeding_provider)

        backend = _make_backend(
            providers=[OIDCProviderConfig(issuer="https://x", audience="y")],
            token_validator=_declining_validator,
        )
        result = await backend.authenticate_request(_connection({"authorization": "Bearer tok"}))

        assert result.auth == {"sub": "via-provider"}

    @pytest.mark.asyncio
    async def test_raises_not_authorized_on_missing_header(self) -> None:
        """Missing Authorization header → NotAuthorizedException with WWW-Authenticate."""
        backend = _make_backend(providers=[OIDCProviderConfig(issuer="https://x", audience="y")])

        with pytest.raises(NotAuthorizedException) as excinfo:
            await backend.authenticate_request(_connection({}))

        assert excinfo.value.headers is not None
        assert excinfo.value.headers.get("WWW-Authenticate") == "Bearer"

    @pytest.mark.asyncio
    async def test_raises_not_authorized_on_invalid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid header shape but no validator/provider accepts it → NotAuthorizedException."""

        async def _declining_validator(token: str) -> dict[str, Any] | None:
            return None

        async def _declining_provider(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
            return None

        monkeypatch.setattr(backend_mod, "_validate_with_oidc_provider", _declining_provider)

        backend = _make_backend(
            providers=[OIDCProviderConfig(issuer="https://x", audience="y")],
            token_validator=_declining_validator,
        )

        with pytest.raises(NotAuthorizedException):
            await backend.authenticate_request(_connection({"authorization": "Bearer bad"}))

    @pytest.mark.asyncio
    async def test_user_resolver_runs_on_success(self) -> None:
        """When user_resolver is set, it maps claims → user."""

        async def _validator(token: str) -> dict[str, Any] | None:
            return {"sub": "alice", "email": "a@example.com"}

        def _sync_resolver(claims: dict[str, Any], app: Any) -> dict[str, Any]:
            return {"id": claims["sub"], "email": claims["email"]}

        backend = _make_backend(token_validator=_validator, user_resolver=_sync_resolver)

        result = await backend.authenticate_request(_connection({"authorization": "Bearer tok"}))
        assert result.user == {"id": "alice", "email": "a@example.com"}
        assert result.auth == {"sub": "alice", "email": "a@example.com"}

    @pytest.mark.asyncio
    async def test_async_user_resolver(self) -> None:
        """Async user_resolver is awaited."""

        async def _validator(token: str) -> dict[str, Any] | None:
            return {"sub": "bob"}

        async def _async_resolver(claims: dict[str, Any], app: Any) -> str:
            return f"user:{claims['sub']}"

        backend = _make_backend(token_validator=_validator, user_resolver=_async_resolver)

        result = await backend.authenticate_request(_connection({"authorization": "Bearer tok"}))
        assert result.user == "user:bob"

    @pytest.mark.asyncio
    async def test_user_resolver_optional(self) -> None:
        """Without user_resolver, AuthenticationResult.user is None and auth is claims dict."""

        async def _validator(token: str) -> dict[str, Any] | None:
            return {"sub": "alice"}

        backend = _make_backend(token_validator=_validator)

        result = await backend.authenticate_request(_connection({"authorization": "Bearer tok"}))
        assert result.user is None
        assert result.auth == {"sub": "alice"}
