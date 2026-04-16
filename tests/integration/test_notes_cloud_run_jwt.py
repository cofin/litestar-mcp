"""Integration coverage for the Cloud Run-shaped SQLSpec JWT example.

This example keeps ordinary application-managed HS256 JWT auth; the Cloud
Run specifics are the env-var driven ``create_app()`` factory and the
unauthenticated ``/healthz`` liveness route. These tests exercise both,
without actually deploying to Cloud Run.
"""

from pathlib import Path

import pytest
from docs.examples.notes.shared.auth import mint_hs256_token
from docs.examples.notes.sqlspec.cloud_run_jwt import CloudRunSettings, create_app
from litestar.testing import TestClient

from tests.integration.conftest import parse_tool_payload, rpc, rpc_response

JWT_TEST_SECRET = "cloud-run-jwt-notes-integration-secret-32bytes!"


def _env(tmp_path: Path, *, port: str = "8080") -> dict[str, str]:
    return {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'notes-cloud-run.sqlite'}",
        "JWT_SIGNING_KEY": JWT_TEST_SECRET,
        "JWT_ISSUER": "http://localhost:8000/auth",
        "JWT_AUDIENCE": "http://localhost:8000/api",
        "PORT": port,
        "LOG_LEVEL": "warning",
    }


def test_settings_require_signing_key() -> None:
    """The factory must refuse to boot without JWT_SIGNING_KEY."""
    with pytest.raises(RuntimeError, match="JWT_SIGNING_KEY"):
        CloudRunSettings.from_env({"DATABASE_URL": "sqlite:///:memory:"})


def test_healthz_is_unauthenticated(tmp_path: Path) -> None:
    """Cloud Run liveness probe must succeed without credentials."""
    settings = CloudRunSettings.from_env(_env(tmp_path))
    app = create_app(settings)
    with TestClient(app=app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_mcp_rejects_unauthenticated(tmp_path: Path) -> None:
    """Unauthenticated MCP calls must be rejected with 401."""
    settings = CloudRunSettings.from_env(_env(tmp_path))
    app = create_app(settings)
    with TestClient(app=app) as client:
        response = rpc_response(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
        )
        assert response.status_code == 401


def test_login_issues_token_and_mcp_accepts_it(tmp_path: Path) -> None:
    """/auth/login must issue a token the MCP surface accepts."""
    settings = CloudRunSettings.from_env(_env(tmp_path))
    app = create_app(settings)

    with TestClient(app=app) as client:
        login = client.post(
            "/auth/login/",
            json={"username": "alice", "password": "alice-password"},
        )
        assert login.status_code == 201
        token = login.json()["access_token"]

        created = rpc(
            client,
            "tools/call",
            {"name": "create_note", "arguments": {"data": {"title": "hi", "body": "cloud run"}}},
            headers={"Authorization": f"Bearer {token}"},
        )
        payload = parse_tool_payload(created)
        note_id = payload["id"]

        listed = rpc(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        listed_payload = parse_tool_payload(listed)
        items = listed_payload.get("items") if isinstance(listed_payload, dict) else listed_payload
        assert isinstance(items, list)
        assert any(item["id"] == note_id for item in items)


def test_mcp_isolates_notes_across_subs(tmp_path: Path) -> None:
    """A token for a different principal must not see another's notes."""
    settings = CloudRunSettings.from_env(_env(tmp_path))
    app = create_app(settings)

    token_alice = mint_hs256_token("alice", secret=JWT_TEST_SECRET)
    token_bob = mint_hs256_token("bob", secret=JWT_TEST_SECRET)

    with TestClient(app=app) as client:
        created = rpc(
            client,
            "tools/call",
            {"name": "create_note", "arguments": {"data": {"title": "secret", "body": "alice only"}}},
            headers={"Authorization": f"Bearer {token_alice}"},
        )
        alice_note_id = parse_tool_payload(created)["id"]

        bob_listed = rpc(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers={"Authorization": f"Bearer {token_bob}"},
        )
        bob_payload = parse_tool_payload(bob_listed)
        bob_items = bob_payload.get("items") if isinstance(bob_payload, dict) else bob_payload
        assert isinstance(bob_items, list)
        assert not any(item["id"] == alice_note_id for item in bob_items)
