"""JWT-authenticated Advanced Alchemy reference notes example.

Scopes notes by the validated ``sub`` claim of the bearer token. The app
exposes a tiny ``/auth/login`` controller that accepts a username/password
pair and returns an HS256-signed access token, so the example is runnable
end-to-end without extra infrastructure.

The same variant is reused by :mod:`docs.examples.notes.advanced_alchemy.jwt_auth_dishka`;
see that module for the Dishka-backed variant.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "litestar[standard]>=2.0",
#   "litestar-mcp",
#   "advanced-alchemy[litestar]>=1.0",
#   "aiosqlite",
#   "python-jose[cryptography]",
#   "uvicorn",
# ]
# ///

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
from litestar import Controller, Litestar, Request, delete, get, post
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

DEFAULT_USER_DIRECTORY = {"alice": "alice-password", "bob": "bob-password"}


def create_app(
    database_path: str | None = None,
    *,
    token_secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    user_directory: dict[str, str] | None = None,
) -> Litestar:
    """Create the JWT-authenticated Advanced Alchemy reference notes app.

    Args:
        database_path: Optional SQLite file path. When omitted, a
            ``.reference-notes-aa-jwt.sqlite`` file in the current working
            directory is used.
        token_secret: HS256 secret used by both the login endpoint and the
            token validator. Callers MUST pass a stable secret; there is no
            default so the example fails loudly rather than signing tokens
            with a placeholder.
        issuer: JWT ``iss`` claim. Defaults to the locked foundation value.
        audience: JWT ``aud`` claim. Defaults to the locked foundation value.
        user_directory: Optional mapping of ``sub -> password`` used by the
            demo login controller. Defaults to a pair of demo users.
    """
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-aa-jwt.sqlite")
    alchemy_config = SQLAlchemyAsyncConfig(
        connection_string=f"sqlite+aiosqlite:///{sqlite_path}",
        create_all=True,
        before_send_handler="autocommit",
        session_config=AsyncSessionConfig(expire_on_commit=False),
    )

    def _sign(sub: str) -> str:
        return mint_hs256_token(sub, secret=token_secret, issuer=issuer, audience=audience)

    login_controller = build_login_controller(
        user_directory=user_directory or DEFAULT_USER_DIRECTORY,
        token_signer=_sign,
    )
    oauth_backend = build_oauth_backend(secret=token_secret, issuer=issuer, audience=audience)

    async def _provide_resolved_user(request: Request[Any, Any, Any]) -> AuthenticatedIdentity:
        """HTTP-side dependency that exposes the same identity the MCP executor injects."""
        user = request.user
        if not isinstance(user, AuthenticatedIdentity):
            msg = "Authenticated identity is required for this endpoint"
            raise NotAuthorizedException(msg)
        return user

    note_dependencies = {
        **providers.create_service_dependencies(NoteService, "note_service", config=alchemy_config),
        "resolved_user": Provide(_provide_resolved_user),
    }

    class NoteController(Controller):
        path = "/notes"
        dependencies = note_dependencies

        @get("/", opt={"mcp_tool": LIST_NOTES_TOOL_NAME})
        async def list_notes(
            self, note_service: NoteService, resolved_user: AuthenticatedIdentity
        ) -> OffsetPagination[Note]:
            notes = await note_service.list(NoteRecord.owner_sub == resolved_user.sub)
            return note_service.to_schema(notes, schema_type=Note)

        @post("/", opt={"mcp_tool": CREATE_NOTE_TOOL_NAME})
        async def create_note(
            self,
            data: dict[str, Any],
            note_service: NoteService,
            resolved_user: AuthenticatedIdentity,
        ) -> Note:
            payload = msgspec.convert(data, CreateNoteInput)
            note = await note_service.create(
                {"title": payload.title, "body": payload.body, "owner_sub": resolved_user.sub},
                auto_commit=True,
            )
            return note_service.to_schema(note, schema_type=Note)

        @delete("/{note_id:str}", status_code=HTTP_200_OK, opt={"mcp_tool": DELETE_NOTE_TOOL_NAME})
        async def delete_note(
            self,
            note_id: str,
            note_service: NoteService,
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
        return build_app_info(backend="advanced_alchemy", auth_mode="jwt", supports_dishka=False)

    mcp_config = MCPConfig()
    mcp_config.auth = build_mcp_auth_config(secret=token_secret, issuer=issuer, audience=audience)

    return Litestar(
        route_handlers=[login_controller, NoteController, notes_schema, get_api_info],
        plugins=[SQLAlchemyPlugin(config=alchemy_config), LitestarMCP(mcp_config)],
        on_app_init=[oauth_backend.on_app_init],
    )
