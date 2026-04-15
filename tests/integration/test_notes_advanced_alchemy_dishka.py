"""Integration coverage for the Advanced Alchemy + Dishka notes example."""

import json
from pathlib import Path

from litestar.testing import TestClient

from tests.integration.conftest import parse_tool_payload, rpc


def test_notes_advanced_alchemy_dishka_example_round_trip(tmp_path: Path) -> None:
    """The AA+Dishka no-auth notes example exposes the shared contract over MCP."""
    from docs.examples.notes.advanced_alchemy.no_auth_dishka import create_app

    app = create_app(database_path=str(tmp_path / "notes-aa-dishka.sqlite"))
    headers: dict[str, str] = {}

    with TestClient(app=app) as client:
        tools = rpc(client, "tools/list", headers=headers)["result"]["tools"]
        resources = rpc(client, "resources/list", headers=headers)["result"]["resources"]

        tool_names = {tool["name"] for tool in tools}
        assert {"list_notes", "create_note", "delete_note"}.issubset(tool_names)

        resource_names = {resource["name"] for resource in resources}
        assert {"notes_schema", "app_info"}.issubset(resource_names)

        app_info_resource = rpc(
            client,
            "resources/read",
            {"uri": "litestar://app_info"},
            headers=headers,
        )
        app_info_payload = json.loads(app_info_resource["result"]["contents"][0]["text"])

        assert app_info_payload["backend"] == "advanced_alchemy"
        assert app_info_payload["supports_dishka"] is True
        assert app_info_payload["auth_mode"] == "none"

        created = rpc(
            client,
            "tools/call",
            {"name": "create_note", "arguments": {"data": {"title": "Gamma", "body": "Dishka note"}}},
            headers=headers,
        )
        created_payload = parse_tool_payload(created)
        note_id = created_payload["id"]

        assert created_payload["title"] == "Gamma"
        assert created_payload["body"] == "Dishka note"

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
