"""Integration coverage for the SQLSpec JWT-scoped notes examples.

Covers both the plain Litestar-DI (``jwt_auth``) variant and the Dishka-backed
(``jwt_auth_dishka``) variant. Both variants must enforce bearer auth on MCP
calls and scope notes by the validated JWT ``sub`` claim.
"""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from docs.examples.notes.shared.auth import mint_hs256_token
from litestar.testing import TestClient

from tests.integration.conftest import parse_tool_payload, rpc, rpc_response

JWT_TEST_SECRET = "jwt-notes-sqlspec-integration-secret-32bytes!"


VARIANTS = [
    pytest.param(False, id="dishka-off"),
    pytest.param(True, id="dishka-on"),
]


def _load_create_app(*, dishka: bool) -> Any:
    module_name = "docs.examples.notes.sqlspec.jwt_auth_dishka" if dishka else "docs.examples.notes.sqlspec.jwt_auth"
    module = importlib.import_module(module_name)
    return module.create_app


@pytest.mark.parametrize("dishka", VARIANTS)
def test_notes_sqlspec_jwt_rejects_unauthenticated(tmp_path: Path, dishka: bool) -> None:
    """Unauthenticated MCP calls should be rejected with 401."""
    create_app = _load_create_app(dishka=dishka)
    app = create_app(
        database_path=str(tmp_path / f"notes-sqlspec-jwt-{dishka}.sqlite"),
        token_secret=JWT_TEST_SECRET,
    )

    with TestClient(app=app) as client:
        response = rpc_response(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
        )
        assert response.status_code == 401


@pytest.mark.parametrize("dishka", VARIANTS)
def test_notes_sqlspec_jwt_scopes_notes_by_sub(tmp_path: Path, dishka: bool) -> None:
    """A valid token should allow its principal to create and list its own notes."""
    create_app = _load_create_app(dishka=dishka)
    app = create_app(
        database_path=str(tmp_path / f"notes-sqlspec-jwt-scoped-{dishka}.sqlite"),
        token_secret=JWT_TEST_SECRET,
    )

    token_alice = mint_hs256_token("alice", secret=JWT_TEST_SECRET)
    headers_alice = {"Authorization": f"Bearer {token_alice}"}

    with TestClient(app=app) as client:
        created = rpc(
            client,
            "tools/call",
            {"name": "create_note", "arguments": {"data": {"title": "Alice", "body": "hello"}}},
            headers=headers_alice,
        )
        payload = parse_tool_payload(created)
        note_id = payload["id"]

        listed = rpc(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers=headers_alice,
        )
        listed_payload = parse_tool_payload(listed)
        items = listed_payload.get("items") if isinstance(listed_payload, dict) else listed_payload
        assert isinstance(items, list)
        assert any(item["id"] == note_id for item in items)


@pytest.mark.parametrize("dishka", VARIANTS)
def test_notes_sqlspec_jwt_isolates_notes_across_subs(tmp_path: Path, dishka: bool) -> None:
    """A different principal must not see or delete another principal's notes."""
    create_app = _load_create_app(dishka=dishka)
    app = create_app(
        database_path=str(tmp_path / f"notes-sqlspec-jwt-isolated-{dishka}.sqlite"),
        token_secret=JWT_TEST_SECRET,
    )

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
        bob_listed_payload = parse_tool_payload(bob_listed)
        bob_items = bob_listed_payload.get("items") if isinstance(bob_listed_payload, dict) else bob_listed_payload
        assert isinstance(bob_items, list)
        assert not any(item["id"] == alice_note_id for item in bob_items)

        bob_delete = rpc(
            client,
            "tools/call",
            {"name": "delete_note", "arguments": {"note_id": alice_note_id}},
            headers={"Authorization": f"Bearer {token_bob}"},
        )
        # Delete must either report not-deleted or raise an MCP error; in no
        # case must the note disappear from Alice's view.
        bob_delete_result = bob_delete.get("result")
        if bob_delete_result is not None:
            content = bob_delete_result.get("content", [])
            if content:
                body = json.loads(content[0]["text"])
                assert body.get("deleted") is not True

        alice_listed = rpc(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers={"Authorization": f"Bearer {token_alice}"},
        )
        alice_listed_payload = parse_tool_payload(alice_listed)
        alice_items = (
            alice_listed_payload.get("items") if isinstance(alice_listed_payload, dict) else alice_listed_payload
        )
        assert isinstance(alice_items, list)
        assert any(item["id"] == alice_note_id for item in alice_items)
