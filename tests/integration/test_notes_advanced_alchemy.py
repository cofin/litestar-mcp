"""Integration coverage for the Advanced Alchemy reference notes example."""

import json
from pathlib import Path

from litestar.testing import TestClient

from tests.integration.conftest import parse_tool_payload, rpc


def test_reference_notes_advanced_alchemy_no_auth_example_round_trip(tmp_path: Path) -> None:
    """The no-auth AA example should expose the shared notes contract over MCP."""
    from docs.examples.reference_notes.advanced_alchemy.no_auth import create_app

    app = create_app(database_path=str(tmp_path / "reference-notes-aa.sqlite"))

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]
        resources = rpc(client, "resources/list")["result"]["resources"]

        assert any(tool["name"] == "list_notes" for tool in tools)
        assert any(tool["name"] == "create_note" for tool in tools)
        assert any(tool["name"] == "delete_note" for tool in tools)
        assert any(resource["name"] == "notes_schema" for resource in resources)
        assert any(resource["name"] == "app_info" for resource in resources)

        schema_resource = rpc(client, "resources/read", {"uri": "litestar://notes_schema"})
        schema_payload = json.loads(schema_resource["result"]["contents"][0]["text"])

        assert schema_payload["entity"] == "Note"
        assert schema_payload["tools"] == ["list_notes", "create_note", "delete_note"]

        app_info_resource = rpc(client, "resources/read", {"uri": "litestar://app_info"})
        app_info_payload = json.loads(app_info_resource["result"]["contents"][0]["text"])

        assert app_info_payload["backend"] == "advanced_alchemy"
        assert app_info_payload["auth_mode"] == "none"

        created = rpc(
            client,
            "tools/call",
            {"name": "create_note", "arguments": {"data": {"title": "Alpha", "body": "First note"}}},
        )
        created_payload = parse_tool_payload(created)
        note_id = created_payload["id"]

        assert created_payload["title"] == "Alpha"
        assert created_payload["body"] == "First note"

        listed = rpc(client, "tools/call", {"name": "list_notes", "arguments": {}})
        listed_payload = parse_tool_payload(listed)

        assert any(item["id"] == note_id for item in listed_payload["items"])

        deleted = rpc(client, "tools/call", {"name": "delete_note", "arguments": {"note_id": note_id}})
        deleted_payload = parse_tool_payload(deleted)

        assert deleted_payload == {"deleted": True, "note_id": note_id}
