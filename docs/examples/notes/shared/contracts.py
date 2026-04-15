"""Shared contracts for the reference notes examples."""

from typing import Final
from uuid import UUID

import msgspec

LIST_NOTES_TOOL_NAME: Final = "list_notes"
CREATE_NOTE_TOOL_NAME: Final = "create_note"
DELETE_NOTE_TOOL_NAME: Final = "delete_note"
NOTES_SCHEMA_RESOURCE_NAME: Final = "notes_schema"
APP_INFO_RESOURCE_NAME: Final = "app_info"


class CreateNoteInput(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """Input payload for creating a note."""

    title: str
    body: str


class DeleteNoteInput(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """Input payload for deleting a note."""

    note_id: str


class Note(msgspec.Struct, kw_only=True):
    """Public note shape shared by every example variant."""

    id: UUID
    title: str
    body: str


class DeleteNoteResult(msgspec.Struct, kw_only=True):
    """Result payload for note deletion."""

    deleted: bool
    note_id: str


class NotesSchema(msgspec.Struct, kw_only=True):
    """Shape exposed by the shared notes schema resource."""

    entity: str = "Note"
    fields: tuple[str, ...] = ("id", "title", "body")
    tools: tuple[str, ...] = (
        LIST_NOTES_TOOL_NAME,
        CREATE_NOTE_TOOL_NAME,
        DELETE_NOTE_TOOL_NAME,
    )
    resources: tuple[str, ...] = (
        NOTES_SCHEMA_RESOURCE_NAME,
        APP_INFO_RESOURCE_NAME,
    )


class AppInfo(msgspec.Struct, kw_only=True):
    """Metadata exposed by the shared app info resource."""

    name: str
    backend: str
    auth_mode: str
    supports_dishka: bool = False


def build_app_info(
    *,
    backend: str,
    auth_mode: str,
    supports_dishka: bool = False,
    name: str = "notes",
) -> AppInfo:
    """Construct the shared ``app_info`` payload for reference note variants.

    Every variant MUST call this helper rather than constructing
    :class:`AppInfo` inline so cross-variant diffs stay focused on the
    teachable deltas (backend, auth, DI).
    """
    return AppInfo(
        name=name,
        backend=backend,
        auth_mode=auth_mode,
        supports_dishka=supports_dishka,
    )
