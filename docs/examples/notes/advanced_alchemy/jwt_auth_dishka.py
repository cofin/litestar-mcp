"""JWT-authenticated Advanced Alchemy + Dishka reference notes example.

Same public behavior as :mod:`docs.examples.notes.advanced_alchemy.jwt_auth`,
but the :class:`NoteService` is resolved through a Dishka container. The
authenticated identity still comes from Litestar's normal auth surface —
Dishka is only responsible for domain-service wiring.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    build_mcp_auth_config,
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
from litestar_mcp.executor import ToolExecutionContext

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

    login_controller = build_login_controller(
        user_directory=user_directory or DEFAULT_USER_DIRECTORY,
        token_signer=_sign,
    )
    oauth_backend = build_oauth_backend(secret=token_secret, issuer=issuer, audience=audience)

    async def _provide_resolved_user(request: Request[Any, Any, Any]) -> AuthenticatedIdentity:
        user = request.user
        if not isinstance(user, AuthenticatedIdentity):
            msg = "Authenticated identity is required for this endpoint"
            raise NotAuthorizedException(msg)
        return user

    def _unexpected() -> Any:
        msg = "note_service must be provided by Dishka (HTTP) or the MCP dependency provider (tools)"
        raise RuntimeError(msg)

    handler_dependencies = {
        "note_service": Provide(_unexpected, sync_to_thread=False),
        "resolved_user": Provide(_provide_resolved_user),
    }

    @get("/notes", opt={"mcp_tool": LIST_NOTES_TOOL_NAME}, dependencies=handler_dependencies)
    @inject
    async def list_notes(
        note_service: FromDishka[NoteService], resolved_user: AuthenticatedIdentity
    ) -> OffsetPagination[Note]:
        notes = await note_service.list(NoteRecord.owner_sub == resolved_user.sub)
        return note_service.to_schema(notes, schema_type=Note)

    @post("/notes", opt={"mcp_tool": CREATE_NOTE_TOOL_NAME}, dependencies=handler_dependencies)
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
        dependencies=handler_dependencies,
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

    @asynccontextmanager
    async def mcp_dependency_provider(context: ToolExecutionContext) -> AsyncIterator[dict[str, Any]]:
        """Resolve tool dependencies from the Dishka container for MCP tool calls."""
        opt = getattr(context.handler, "opt", {}) or {}
        if opt.get("mcp_tool") not in {LIST_NOTES_TOOL_NAME, CREATE_NOTE_TOOL_NAME, DELETE_NOTE_TOOL_NAME}:
            yield {}
            return
        async with container() as request_container:
            yield {"note_service": await request_container.get(NoteService)}

    mcp_config = MCPConfig(dependency_provider=mcp_dependency_provider)
    mcp_config.auth = build_mcp_auth_config(secret=token_secret, issuer=issuer, audience=audience)

    async def close_container() -> None:
        await container.close()

    app = Litestar(
        route_handlers=[login_controller, list_notes, create_note, delete_note, notes_schema, get_api_info],
        plugins=[SQLAlchemyPlugin(config=alchemy_config), LitestarMCP(mcp_config)],
        on_app_init=[oauth_backend.on_app_init],
        on_shutdown=[close_container],
    )
    setup_dishka(container, app)
    return app
