"""JWT-authenticated Advanced Alchemy + Dishka reference notes example."""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "litestar[standard]>=2.0",
#   "litestar-mcp",
#   "advanced-alchemy[litestar]>=1.0",
#   "aiosqlite",
#   "dishka",
#   "python-jose[cryptography]",
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
from litestar import Litestar, Request, delete, get, post
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException
from litestar.status_codes import HTTP_200_OK

from docs.examples.notes.advanced_alchemy.common import NoteRecord, NoteService
from docs.examples.notes.shared.auth import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    AuthenticatedIdentity,
    build_login_controller,
    build_mcp_auth_metadata,
    build_oauth_backend,
    mint_hs256_token,
)
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

DEFAULT_USER_DIRECTORY = {"alice": "alice-password", "bob": "bob-password"}


class NotesDishkaProvider(Provider):
    """Provide request-scoped AA sessions + the :class:`NoteService`."""

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


def create_app(
    database_path: str | None = None,
    *,
    token_secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    user_directory: dict[str, str] | None = None,
) -> Litestar:
    """Create the Dishka + JWT Advanced Alchemy reference notes app."""
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-aa-jwt-dishka.sqlite")
    alchemy_config = SQLAlchemyAsyncConfig(
        connection_string=f"sqlite+aiosqlite:///{sqlite_path}",
        create_all=True,
        before_send_handler="autocommit",
        session_config=AsyncSessionConfig(expire_on_commit=False),
    )
    container = make_async_container(LitestarProvider(), NotesDishkaProvider(alchemy_config))

    def _sign(sub: str) -> str:
        return mint_hs256_token(sub, secret=token_secret, issuer=issuer, audience=audience)

    oauth_backend = build_oauth_backend(secret=token_secret, issuer=issuer, audience=audience)

    async def _provide_resolved_user(request: Request[Any, Any, Any]) -> AuthenticatedIdentity:
        user = request.user
        if not isinstance(user, AuthenticatedIdentity):
            msg = "Authenticated identity is required"
            raise NotAuthorizedException(msg)
        return user

    resolved_user_dep = {"resolved_user": Provide(_provide_resolved_user)}

    @get("/notes", opt={"mcp_tool": LIST_NOTES_TOOL_NAME}, dependencies=resolved_user_dep)
    @inject
    async def list_notes(
        note_service: FromDishka[NoteService], resolved_user: AuthenticatedIdentity
    ) -> OffsetPagination[Note]:
        notes = await note_service.list(NoteRecord.owner_sub == resolved_user.sub)
        return note_service.to_schema(notes, schema_type=Note)

    @post("/notes", opt={"mcp_tool": CREATE_NOTE_TOOL_NAME}, dependencies=resolved_user_dep)
    @inject
    async def create_note(
        data: dict[str, Any],
        note_service: FromDishka[NoteService],
        resolved_user: AuthenticatedIdentity,
    ) -> Note:
        payload = msgspec.convert(data, CreateNoteInput)
        note = await note_service.create(
            {"title": payload.title, "body": payload.body, "owner_sub": resolved_user.sub},
            auto_commit=True,
        )
        return note_service.to_schema(note, schema_type=Note)

    @delete(
        "/notes/{note_id:str}",
        status_code=HTTP_200_OK,
        opt={"mcp_tool": DELETE_NOTE_TOOL_NAME},
        dependencies=resolved_user_dep,
    )
    @inject
    async def delete_note(
        note_id: str,
        note_service: FromDishka[NoteService],
        resolved_user: AuthenticatedIdentity,
    ) -> DeleteNoteResult:
        existing = await note_service.get_one_or_none(
            NoteRecord.id == UUID(note_id), NoteRecord.owner_sub == resolved_user.sub
        )
        if existing is None:
            return DeleteNoteResult(deleted=False, note_id=note_id)
        await note_service.delete(existing.id, auto_commit=True)
        return DeleteNoteResult(deleted=True, note_id=note_id)

    @get("/notes/schema", opt={"mcp_resource": NOTES_SCHEMA_RESOURCE_NAME}, sync_to_thread=False)
    def notes_schema() -> NotesSchema:
        return NotesSchema()

    @get("/app/info", opt={"mcp_resource": APP_INFO_RESOURCE_NAME}, sync_to_thread=False)
    def get_api_info() -> AppInfo:
        return build_app_info(backend="advanced_alchemy", auth_mode="jwt", supports_dishka=True)

    mcp_config = MCPConfig(auth=build_mcp_auth_metadata(issuer=issuer, audience=audience))

    app = Litestar(
        route_handlers=[
            build_login_controller(user_directory=user_directory or DEFAULT_USER_DIRECTORY, token_signer=_sign),
            list_notes,
            create_note,
            delete_note,
            notes_schema,
            get_api_info,
        ],
        plugins=[SQLAlchemyPlugin(config=alchemy_config), LitestarMCP(mcp_config)],
        on_app_init=[oauth_backend.on_app_init],
        on_shutdown=[container.close],
    )
    setup_dishka(container, app)
    return app
