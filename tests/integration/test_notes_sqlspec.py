"""Integration coverage for the SQLSpec notes reference examples.

Parametrized over ``(dishka, auth_mode)`` so the same round-trip assertion
exercises all four plain+dishka x no-auth+bearer variants. Each test uses a
fresh ``tmp_path`` SQLite file so schema bootstrap is idempotent per-test
and no cross-test state leaks through the database file.
"""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from litestar.testing import TestClient

from tests.integration.apps import AuthMode
from tests.integration.conftest import AUTH_MODES, auth_headers, parse_tool_payload, rpc

VARIANTS = [
    pytest.param(False, id="dishka-off"),
    pytest.param(True, id="dishka-on"),
]


def _load_create_app(*, dishka: bool) -> Any:
    module_name = "docs.examples.notes.sqlspec.no_auth_dishka" if dishka else "docs.examples.notes.sqlspec.no_auth"
    module = importlib.import_module(module_name)
    return module.create_app


@pytest.mark.parametrize("dishka", VARIANTS)
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
def test_notes_sqlspec_round_trip(tmp_path: Path, dishka: bool, auth_mode: AuthMode) -> None:
    """Every SQLSpec notes variant exposes the shared notes contract over MCP."""
    create_app = _load_create_app(dishka=dishka)
    app = create_app(
        database_path=str(tmp_path / f"notes-sqlspec-{dishka}-{auth_mode}.sqlite"),
        auth_mode=auth_mode,
    )
    headers = auth_headers(auth_mode)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list", headers=headers)["result"]["tools"]
        resources = rpc(client, "resources/list", headers=headers)["result"]["resources"]

        tool_names = {tool["name"] for tool in tools}
        assert {"list_notes", "create_note", "delete_note"}.issubset(tool_names)

        resource_names = {resource["name"] for resource in resources}
        assert {"notes_schema", "app_info"}.issubset(resource_names)

        schema_resource = rpc(
            client,
            "resources/read",
            {"uri": "litestar://notes_schema"},
            headers=headers,
        )
        schema_payload = json.loads(schema_resource["result"]["contents"][0]["text"])
        assert schema_payload["entity"] == "Note"
        assert schema_payload["tools"] == ["list_notes", "create_note", "delete_note"]

        app_info_resource = rpc(
            client,
            "resources/read",
            {"uri": "litestar://app_info"},
            headers=headers,
        )
        app_info_payload = json.loads(app_info_resource["result"]["contents"][0]["text"])
        assert app_info_payload["backend"] == "sqlspec"
        assert app_info_payload["auth_mode"] == auth_mode
        assert app_info_payload["supports_dishka"] is dishka

        created = rpc(
            client,
            "tools/call",
            {"name": "create_note", "arguments": {"data": {"title": "Alpha", "body": "First note"}}},
            headers=headers,
        )
        created_payload = parse_tool_payload(created)
        note_id = created_payload["id"]
        assert created_payload["title"] == "Alpha"
        assert created_payload["body"] == "First note"

        listed = rpc(client, "tools/call", {"name": "list_notes", "arguments": {}}, headers=headers)
        listed_payload = parse_tool_payload(listed)
        # The plain (no-auth) variants return list-shaped payloads; when the
        # tool payload is a list, the MCP executor wraps it in ``items``.
        items = listed_payload.get("items") if isinstance(listed_payload, dict) else listed_payload
        assert isinstance(items, list)
        assert any(item["id"] == note_id for item in items)

        deleted = rpc(
            client,
            "tools/call",
            {"name": "delete_note", "arguments": {"note_id": note_id}},
            headers=headers,
        )
        deleted_payload = parse_tool_payload(deleted)
        assert deleted_payload == {"deleted": True, "note_id": note_id}
