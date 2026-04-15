"""Integration coverage for the Advanced Alchemy notes reference example."""

import json
from pathlib import Path

import pytest
from litestar.testing import TestClient

from tests.integration.apps import AuthMode
from tests.integration.conftest import AUTH_MODES, auth_headers, parse_tool_payload, rpc


@pytest.mark.parametrize("auth_mode", AUTH_MODES)
def test_notes_advanced_alchemy_example_round_trip(tmp_path: Path, auth_mode: AuthMode) -> None:
    """The AA notes example should expose the shared notes contract over MCP."""
    from docs.examples.notes.advanced_alchemy.no_auth import create_app

    app = create_app(
        database_path=str(tmp_path / f"notes-aa-{auth_mode}.sqlite"),
        auth_mode=auth_mode,
    )
    headers = auth_headers(auth_mode)

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list", headers=headers)["result"]["tools"]
        resources = rpc(client, "resources/list", headers=headers)["result"]["resources"]

        assert any(tool["name"] == "list_notes" for tool in tools)
        assert any(tool["name"] == "create_note" for tool in tools)
        assert any(tool["name"] == "delete_note" for tool in tools)
        assert any(resource["name"] == "notes_schema" for resource in resources)
        assert any(resource["name"] == "app_info" for resource in resources)

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

        assert app_info_payload["backend"] == "advanced_alchemy"
        assert app_info_payload["auth_mode"] == auth_mode

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

        assert any(item["id"] == note_id for item in listed_payload["items"])

        deleted = rpc(
            client,
            "tools/call",
            {"name": "delete_note", "arguments": {"note_id": note_id}},
            headers=headers,
        )
        deleted_payload = parse_tool_payload(deleted)

        assert deleted_payload == {"deleted": True, "note_id": note_id}
