"""No-auth SQLSpec reference notes example.

Mirrors :mod:`docs.examples.notes.advanced_alchemy.no_auth` but backs the
notes domain with an explicit SQLSpec async SQLite adapter. Parameters are
bound, results are mapped into :class:`NoteRow` via ``schema_type``, and the
HTTP/MCP surface is handed the shared public :class:`Note` shape.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import msgspec
from litestar import Controller, Litestar, delete, get, post
from litestar.di import Provide
from litestar.status_codes import HTTP_200_OK
from sqlspec.extensions.litestar import SQLSpecPlugin

from docs.examples.notes.shared.contracts import (
    APP_INFO_RESOURCE_NAME,
    CREATE_NOTE_TOOL_NAME,
    DELETE_NOTE_TOOL_NAME,
    LIST_NOTES_TOOL_NAME,
    NOTES_SCHEMA_RESOURCE_NAME,
    AppInfo,
    CreateNoteInput,
    DeleteNoteResult,
    Note,
    NotesSchema,
    build_app_info,
)
from docs.examples.notes.sqlspec.common import (
    SQLSpecNoteService,
    bootstrap_schema,
    build_sqlspec,
    note_row_to_public,
    provide_note_service,
)
from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.executor import ToolExecutionContext


def create_app(database_path: str | None = None) -> Litestar:
    """Create the SQLSpec reference notes app (no auth).

    Args:
        database_path: Optional SQLite file path. When omitted, a
            ``.reference-notes-sqlspec.sqlite`` file in the current working
            directory is used.
    """
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-sqlspec.sqlite")
    sqlspec, config = build_sqlspec(str(sqlite_path))

    async def note_service_provider() -> AsyncIterator[SQLSpecNoteService]:
        async with provide_note_service(sqlspec, config) as service:
            yield service

    class NoteController(Controller):
        path = "/notes"
        dependencies = {"note_service": Provide(note_service_provider)}

        @get("/", opt={"mcp_tool": LIST_NOTES_TOOL_NAME})
        async def list_notes(self, note_service: SQLSpecNoteService) -> list[Note]:
            rows = await note_service.list_public()
            return [msgspec.convert(note_row_to_public(row), Note) for row in rows]

        @post("/", opt={"mcp_tool": CREATE_NOTE_TOOL_NAME})
        async def create_note(self, data: dict[str, Any], note_service: SQLSpecNoteService) -> Note:
            payload = msgspec.convert(data, CreateNoteInput)
            row = await note_service.create(title=payload.title, body=payload.body)
            return msgspec.convert(note_row_to_public(row), Note)

        @delete("/{note_id:str}", status_code=HTTP_200_OK, opt={"mcp_tool": DELETE_NOTE_TOOL_NAME})
        async def delete_note(self, note_id: str, note_service: SQLSpecNoteService) -> DeleteNoteResult:
            await note_service.delete(note_id)
            return DeleteNoteResult(deleted=True, note_id=note_id)

    @get("/notes/schema", opt={"mcp_resource": NOTES_SCHEMA_RESOURCE_NAME}, sync_to_thread=False)
    def notes_schema() -> NotesSchema:
        return NotesSchema()

    @get("/app/info", opt={"mcp_resource": APP_INFO_RESOURCE_NAME}, sync_to_thread=False)
    def get_api_info() -> AppInfo:
        return build_app_info(backend="sqlspec", auth_mode="none", supports_dishka=False)

    @asynccontextmanager
    async def mcp_dependency_provider(context: ToolExecutionContext) -> AsyncIterator[dict[str, Any]]:
        """Provide the per-tool ``note_service`` from a fresh SQLSpec session.

        The static ``notes_schema`` / ``app_info`` resources take no extra
        kwargs, so this provider only yields the service for the tool
        handlers that declare it.
        """
        opt = getattr(context.handler, "opt", {}) or {}
        if opt.get("mcp_tool") not in {LIST_NOTES_TOOL_NAME, CREATE_NOTE_TOOL_NAME, DELETE_NOTE_TOOL_NAME}:
            yield {}
            return
        async with provide_note_service(sqlspec, config) as service:
            yield {"note_service": service}

    async def on_startup() -> None:
        await bootstrap_schema(sqlspec, config)

    return Litestar(
        route_handlers=[NoteController, notes_schema, get_api_info],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), LitestarMCP(MCPConfig(dependency_provider=mcp_dependency_provider))],
    )
