"""JWT-authenticated SQLSpec reference notes example."""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "litestar[standard]>=2.0",
#   "litestar-mcp",
#   "sqlspec[aiosqlite]>=0.43",
#   "python-jose[cryptography]",
#   "uvicorn",
# ]
# ///

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import msgspec
from litestar import Controller, Litestar, Request, delete, get, post
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException
from litestar.status_codes import HTTP_200_OK
from sqlspec.extensions.litestar import SQLSpecPlugin

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
from docs.examples.notes.sqlspec.common import (
    SQLSpecNoteService,
    bootstrap_schema,
    build_sqlspec,
    note_row_to_public,
    provide_note_service,
)
from litestar_mcp import LitestarMCP, MCPConfig

DEFAULT_USER_DIRECTORY = {"alice": "alice-password", "bob": "bob-password"}


def create_app(
    database_path: str | None = None,
    *,
    token_secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    user_directory: dict[str, str] | None = None,
) -> Litestar:
    """Create the JWT-authenticated SQLSpec reference notes app."""
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-sqlspec-jwt.sqlite")
    sqlspec, config = build_sqlspec(str(sqlite_path))

    def _sign(sub: str) -> str:
        return mint_hs256_token(sub, secret=token_secret, issuer=issuer, audience=audience)

    oauth_backend = build_oauth_backend(secret=token_secret, issuer=issuer, audience=audience)

    async def _provide_resolved_user(request: Request[Any, Any, Any]) -> AuthenticatedIdentity:
        user = request.user
        if not isinstance(user, AuthenticatedIdentity):
            msg = "Authenticated identity is required"
            raise NotAuthorizedException(msg)
        return user

    async def note_service_provider() -> AsyncIterator[SQLSpecNoteService]:
        async with provide_note_service(sqlspec, config) as service:
            yield service

    class NoteController(Controller):
        path = "/notes"
        dependencies = {
            "note_service": Provide(note_service_provider),
            "resolved_user": Provide(_provide_resolved_user),
        }

        @get("/", opt={"mcp_tool": LIST_NOTES_TOOL_NAME})
        async def list_notes(
            self, note_service: SQLSpecNoteService, resolved_user: AuthenticatedIdentity
        ) -> list[Note]:
            rows = await note_service.list_for_owner(resolved_user.sub)
            return [msgspec.convert(note_row_to_public(row), Note) for row in rows]

        @post("/", opt={"mcp_tool": CREATE_NOTE_TOOL_NAME})
        async def create_note(
            self, data: dict[str, Any], note_service: SQLSpecNoteService, resolved_user: AuthenticatedIdentity
        ) -> Note:
            payload = msgspec.convert(data, CreateNoteInput)
            row = await note_service.create(title=payload.title, body=payload.body, owner_sub=resolved_user.sub)
            return msgspec.convert(note_row_to_public(row), Note)

        @delete("/{note_id:str}", status_code=HTTP_200_OK, opt={"mcp_tool": DELETE_NOTE_TOOL_NAME})
        async def delete_note(
            self, note_id: str, note_service: SQLSpecNoteService, resolved_user: AuthenticatedIdentity
        ) -> DeleteNoteResult:
            deleted = await note_service.delete_for_owner(note_id, resolved_user.sub)
            return DeleteNoteResult(deleted=deleted, note_id=note_id)

    @get("/notes/schema", opt={"mcp_resource": NOTES_SCHEMA_RESOURCE_NAME}, sync_to_thread=False)
    def notes_schema() -> NotesSchema:
        return NotesSchema()

    @get("/app/info", opt={"mcp_resource": APP_INFO_RESOURCE_NAME}, sync_to_thread=False)
    def get_api_info() -> AppInfo:
        return build_app_info(backend="sqlspec", auth_mode="jwt", supports_dishka=False)

    async def on_startup() -> None:
        await bootstrap_schema(sqlspec, config)

    mcp_config = MCPConfig(auth=build_mcp_auth_metadata(issuer=issuer, audience=audience))

    return Litestar(
        route_handlers=[
            build_login_controller(user_directory=user_directory or DEFAULT_USER_DIRECTORY, token_signer=_sign),
            NoteController,
            notes_schema,
            get_api_info,
        ],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), LitestarMCP(mcp_config)],
        on_app_init=[oauth_backend.on_app_init],
    )
