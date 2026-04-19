"""No-auth Advanced Alchemy + Dishka reference notes example.

Mirrors :mod:`docs.examples.notes.advanced_alchemy.no_auth` but resolves the
:class:`NoteService` through a Dishka container rather than Litestar's
built-in DI. The public MCP surface (tool and resource names, payload shapes)
is identical to the plain variant.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "litestar[standard]>=2.0",
#   "litestar-mcp",
#   "advanced-alchemy[litestar]>=1.0",
#   "aiosqlite",
#   "dishka",
#   "uvicorn",
# ]
# ///

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import msgspec
from advanced_alchemy.extensions.litestar import (
    AsyncSessionConfig,
    SQLAlchemyAsyncConfig,
    SQLAlchemyPlugin,
)
from advanced_alchemy.service import OffsetPagination
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.litestar import FromDishka, LitestarProvider, inject, setup_dishka
from litestar import Litestar, delete, get, post
from litestar.status_codes import HTTP_200_OK

from docs.examples.notes.advanced_alchemy.common import NoteService
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
from litestar_mcp import LitestarMCP, MCPConfig


class NotesDishkaProvider(Provider):
    """Provide request-scoped AA sessions + the :class:`NoteService`.

    The provider takes the AA config at construction time and yields a
    request-scoped ``AsyncSession``. Dishka does NOT try to provide Litestar
    primitives (``Request``, ``State``) — those stay on Litestar's side per
    the ``flow:dishka`` boundary rules.
    """

    def __init__(self, alchemy_config: SQLAlchemyAsyncConfig) -> None:
        super().__init__()
        self.alchemy_config = alchemy_config

    @provide(scope=Scope.REQUEST)
    async def provide_db_session(self) -> AsyncIterator[Any]:
        async with self.alchemy_config.get_session() as db_session:
            yield db_session

    @provide(scope=Scope.REQUEST)
    def provide_note_service(self, db_session: Any) -> NoteService:
        return NoteService(session=db_session)


def create_app(database_path: str | None = None) -> Litestar:
    """Create the Dishka-backed Advanced Alchemy reference notes app (no auth).

    Args:
        database_path: Optional SQLite file path. When omitted, a
            ``.reference-notes-aa-dishka.sqlite`` file in the current
            working directory is used.
    """
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-aa-dishka.sqlite")
    alchemy_config = SQLAlchemyAsyncConfig(
        connection_string=f"sqlite+aiosqlite:///{sqlite_path}",
        create_all=True,
        before_send_handler="autocommit",
        session_config=AsyncSessionConfig(expire_on_commit=False),
    )
    container = make_async_container(LitestarProvider(), NotesDishkaProvider(alchemy_config))

    @get("/notes", mcp_tool=LIST_NOTES_TOOL_NAME)
    @inject
    async def list_notes(note_service: FromDishka[NoteService]) -> OffsetPagination[Note]:
        notes = await note_service.list()
        return note_service.to_schema(notes, schema_type=Note)

    @post("/notes", mcp_tool=CREATE_NOTE_TOOL_NAME)
    @inject
    async def create_note(data: dict[str, Any], note_service: FromDishka[NoteService]) -> Note:
        payload = msgspec.convert(data, CreateNoteInput)
        note = await note_service.create({"title": payload.title, "body": payload.body}, auto_commit=True)
        return note_service.to_schema(note, schema_type=Note)

    @delete(
        "/notes/{note_id:str}",
        status_code=HTTP_200_OK,
        mcp_tool=DELETE_NOTE_TOOL_NAME,
    )
    @inject
    async def delete_note(note_id: str, note_service: FromDishka[NoteService]) -> DeleteNoteResult:
        await note_service.delete(UUID(note_id), auto_commit=True)
        return DeleteNoteResult(deleted=True, note_id=note_id)

    @get("/notes/schema", mcp_resource=NOTES_SCHEMA_RESOURCE_NAME, sync_to_thread=False)
    def notes_schema() -> NotesSchema:
        return NotesSchema()

    @get("/app/info", mcp_resource=APP_INFO_RESOURCE_NAME, sync_to_thread=False)
    def get_api_info() -> AppInfo:
        return build_app_info(backend="advanced_alchemy", auth_mode="none", supports_dishka=True)

    async def close_container() -> None:
        await container.close()

    app = Litestar(
        route_handlers=[list_notes, create_note, delete_note, notes_schema, get_api_info],
        plugins=[SQLAlchemyPlugin(config=alchemy_config), LitestarMCP(MCPConfig())],
        on_shutdown=[close_container],
    )
    setup_dishka(container, app)
    return app
