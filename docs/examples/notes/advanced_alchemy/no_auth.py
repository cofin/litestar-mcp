"""No-auth Advanced Alchemy reference notes example."""

from pathlib import Path
from typing import Any
from uuid import UUID

import msgspec
from advanced_alchemy.extensions.litestar import (
    AsyncSessionConfig,
    SQLAlchemyAsyncConfig,
    SQLAlchemyPlugin,
    providers,
)
from advanced_alchemy.service import OffsetPagination
from litestar import Controller, Litestar, delete, get, post
from litestar.status_codes import HTTP_200_OK

from docs.examples.reference_notes.advanced_alchemy.common import NoteService
from docs.examples.reference_notes.shared.contracts import (
    APP_INFO_RESOURCE_NAME,
    AppInfo,
    CREATE_NOTE_TOOL_NAME,
    CreateNoteInput,
    DeleteNoteResult,
    DELETE_NOTE_TOOL_NAME,
    LIST_NOTES_TOOL_NAME,
    Note,
    NOTES_SCHEMA_RESOURCE_NAME,
    NotesSchema,
)
from litestar_mcp import LitestarMCP


def create_app(database_path: str | None = None) -> Litestar:
    """Create the no-auth Advanced Alchemy reference notes app."""
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-aa.sqlite")
    alchemy_config = SQLAlchemyAsyncConfig(
        connection_string=f"sqlite+aiosqlite:///{sqlite_path}",
        create_all=True,
        before_send_handler="autocommit",
        session_config=AsyncSessionConfig(expire_on_commit=False),
    )

    class NoteController(Controller):
        path = "/notes"
        dependencies = providers.create_service_dependencies(NoteService, "note_service", config=alchemy_config)

        @get("/", opt={"mcp_tool": LIST_NOTES_TOOL_NAME})
        async def list_notes(self, note_service: NoteService) -> OffsetPagination[Note]:
            notes = await note_service.list()
            return note_service.to_schema(notes, schema_type=Note)

        @post("/", opt={"mcp_tool": CREATE_NOTE_TOOL_NAME})
        async def create_note(self, data: dict[str, Any], note_service: NoteService) -> Note:
            payload = msgspec.convert(data, CreateNoteInput)
            note = await note_service.create({"title": payload.title, "body": payload.body}, auto_commit=True)
            return note_service.to_schema(note, schema_type=Note)

        @delete("/{note_id:str}", status_code=HTTP_200_OK, opt={"mcp_tool": DELETE_NOTE_TOOL_NAME})
        async def delete_note(self, note_id: str, note_service: NoteService) -> DeleteNoteResult:
            await note_service.delete(UUID(note_id), auto_commit=True)
            return DeleteNoteResult(deleted=True, note_id=note_id)

    @get("/notes/schema", opt={"mcp_resource": NOTES_SCHEMA_RESOURCE_NAME}, sync_to_thread=False)
    def notes_schema() -> NotesSchema:
        return NotesSchema()

    @get("/app/info", opt={"mcp_resource": APP_INFO_RESOURCE_NAME}, sync_to_thread=False)
    def get_api_info() -> AppInfo:
        return AppInfo(
            name="Reference Notes",
            backend="advanced_alchemy",
            auth_mode="none",
            supports_dishka=False,
        )

    return Litestar(
        route_handlers=[NoteController, notes_schema, get_api_info],
        plugins=[SQLAlchemyPlugin(config=alchemy_config), LitestarMCP()],
    )
