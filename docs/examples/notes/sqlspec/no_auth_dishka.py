"""No-auth SQLSpec + Dishka reference notes example.

Mirrors :mod:`docs.examples.notes.sqlspec.no_auth` but resolves the
:class:`SQLSpecNoteService` through a Dishka container. The public MCP
surface (tool/resource names and payload shapes) is identical to the plain
variant — Dishka is a pure DI swap.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "litestar[standard]>=2.0",
#   "litestar-mcp",
#   "sqlspec[aiosqlite]>=0.43",
#   "dishka",
#   "uvicorn",
# ]
# ///

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import msgspec
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.litestar import FromDishka, LitestarProvider, inject, setup_dishka
from litestar import Litestar, delete, get, post
from litestar.status_codes import HTTP_200_OK
from sqlspec import SQLSpec
from sqlspec.adapters.aiosqlite import AiosqliteConfig
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
)
from litestar_mcp import LitestarMCP, MCPConfig


class NotesDishkaProvider(Provider):
    """Provide request-scoped SQLSpec sessions + :class:`SQLSpecNoteService`.

    The provider takes the already-built :class:`SQLSpec` instance and its
    adapter config at construction time, then yields a request-scoped driver
    (the async SQLSpec session). Dishka does NOT try to provide Litestar
    primitives (``Request``, ``State``) — those stay on Litestar's side per
    the ``flow:dishka`` boundary rules.
    """

    def __init__(self, sqlspec: SQLSpec, config: AiosqliteConfig) -> None:
        super().__init__()
        self.sqlspec = sqlspec
        self.config = config

    @provide(scope=Scope.REQUEST)
    async def provide_db_session(self) -> AsyncIterator[Any]:
        async with self.sqlspec.provide_session(self.config) as db_session:
            yield db_session

    @provide(scope=Scope.REQUEST)
    def provide_note_service(self, db_session: Any) -> SQLSpecNoteService:
        return SQLSpecNoteService(db_session)


def create_app(database_path: str | None = None) -> Litestar:
    """Create the Dishka-backed SQLSpec reference notes app (no auth)."""
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-sqlspec-dishka.sqlite")
    sqlspec, config = build_sqlspec(str(sqlite_path))
    container = make_async_container(LitestarProvider(), NotesDishkaProvider(sqlspec, config))

    @get("/notes", mcp_tool=LIST_NOTES_TOOL_NAME)
    @inject
    async def list_notes(note_service: FromDishka[SQLSpecNoteService]) -> list[Note]:
        rows = await note_service.list_public()
        return [msgspec.convert(note_row_to_public(row), Note) for row in rows]

    @post("/notes", mcp_tool=CREATE_NOTE_TOOL_NAME)
    @inject
    async def create_note(data: dict[str, Any], note_service: FromDishka[SQLSpecNoteService]) -> Note:
        payload = msgspec.convert(data, CreateNoteInput)
        row = await note_service.create(title=payload.title, body=payload.body)
        return msgspec.convert(note_row_to_public(row), Note)

    @delete("/notes/{note_id:str}", status_code=HTTP_200_OK, mcp_tool=DELETE_NOTE_TOOL_NAME)
    @inject
    async def delete_note(note_id: str, note_service: FromDishka[SQLSpecNoteService]) -> DeleteNoteResult:
        await note_service.delete(note_id)
        return DeleteNoteResult(deleted=True, note_id=note_id)

    @get("/notes/schema", mcp_resource=NOTES_SCHEMA_RESOURCE_NAME, sync_to_thread=False)
    def notes_schema() -> NotesSchema:
        return NotesSchema()

    @get("/app/info", mcp_resource=APP_INFO_RESOURCE_NAME, sync_to_thread=False)
    def get_api_info() -> AppInfo:
        return build_app_info(backend="sqlspec", auth_mode="none", supports_dishka=True)

    async def on_startup() -> None:
        await bootstrap_schema(sqlspec, config)

    async def close_container() -> None:
        await container.close()

    app = Litestar(
        route_handlers=[list_notes, create_note, delete_note, notes_schema, get_api_info],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), LitestarMCP(MCPConfig())],
        on_shutdown=[close_container],
    )
    setup_dishka(container, app)
    return app
