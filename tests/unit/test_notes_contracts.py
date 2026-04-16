"""Tests for the shared reference notes example contracts."""

import msgspec


def test_reference_notes_shared_contract_defines_stable_mcp_surface() -> None:
    """The shared notes contract should expose stable MCP names and msgspec shapes."""
    from docs.examples.notes.shared.contracts import (
        APP_INFO_RESOURCE_NAME,
        CREATE_NOTE_TOOL_NAME,
        DELETE_NOTE_TOOL_NAME,
        LIST_NOTES_TOOL_NAME,
        NOTES_SCHEMA_RESOURCE_NAME,
        AppInfo,
        CreateNoteInput,
        Note,
        NotesSchema,
    )

    assert LIST_NOTES_TOOL_NAME == "list_notes"
    assert CREATE_NOTE_TOOL_NAME == "create_note"
    assert DELETE_NOTE_TOOL_NAME == "delete_note"
    assert NOTES_SCHEMA_RESOURCE_NAME == "notes_schema"
    assert APP_INFO_RESOURCE_NAME == "app_info"

    assert issubclass(CreateNoteInput, msgspec.Struct)
    assert issubclass(Note, msgspec.Struct)
    assert issubclass(NotesSchema, msgspec.Struct)
    assert issubclass(AppInfo, msgspec.Struct)

    payload = CreateNoteInput(title="Hello", body="World")
    assert payload.title == "Hello"
    assert payload.body == "World"


def test_reference_notes_shared_auth_normalizes_validated_identity_claims() -> None:
    """Validated JWT and IAP claims should map to one shared identity shape."""
    from docs.examples.notes.shared.auth import AuthenticatedIdentity, identity_from_claims

    identity = identity_from_claims(
        {
            "sub": "user-123",
            "email": "accounts.google.com:alice@example.com",
        }
    )

    assert isinstance(identity, AuthenticatedIdentity)
    assert identity.sub == "user-123"
    assert identity.email == "alice@example.com"
