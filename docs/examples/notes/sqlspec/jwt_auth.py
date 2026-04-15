"""JWT-authenticated SQLSpec reference notes example.

Scopes notes by the validated ``sub`` claim of the bearer token. Mirrors the
Advanced Alchemy :mod:`docs.examples.notes.advanced_alchemy.jwt_auth` variant
but uses typed SQLSpec queries (all parameterized, all ``schema_type``-mapped)
instead of an ORM service.

Each MCP tool call receives a fresh request-scoped SQLSpec session, plus the
:class:`AuthenticatedIdentity` resolved from the bearer token by the plugin's
``user_resolver``. HTTP handlers receive the same identity via
``request.user``.
"""

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
from contextlib import asynccontextmanager
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
from docs.examples.notes.sqlspec.common import (
    SQLSpecNoteService,
    bootstrap_schema,
    build_sqlspec,
    note_row_to_public,
    provide_note_service,
)
from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.executor import ToolExecutionContext

DEFAULT_USER_DIRECTORY = {"alice": "alice-password", "bob": "bob-password"}


def create_app(
    database_path: str | None = None,
    *,
    token_secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    user_directory: dict[str, str] | None = None,
) -> Litestar:
    """Create the JWT-authenticated SQLSpec reference notes app.

    Args:
        database_path: Optional SQLite file path. When omitted, a
            ``.reference-notes-sqlspec-jwt.sqlite`` file in the current
            working directory is used.
        token_secret: HS256 secret shared by the login endpoint and the
            token validator. No default; callers MUST supply a stable
            secret so the example fails loudly rather than signing tokens
            with a placeholder.
        issuer: JWT ``iss`` claim. Defaults to the locked foundation value.
        audience: JWT ``aud`` claim. Defaults to the locked foundation value.
        user_directory: Optional ``sub -> password`` mapping used by the
            demo login controller. Defaults to a pair of demo users.
    """
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-sqlspec-jwt.sqlite")
    sqlspec, config = build_sqlspec(str(sqlite_path))

    def _sign(sub: str) -> str:
        return mint_hs256_token(sub, secret=token_secret, issuer=issuer, audience=audience)

    login_controller = build_login_controller(
        user_directory=user_directory or DEFAULT_USER_DIRECTORY,
        token_signer=_sign,
    )
    oauth_backend = build_oauth_backend(secret=token_secret, issuer=issuer, audience=audience)

    async def _provide_resolved_user(request: Request[Any, Any, Any]) -> AuthenticatedIdentity:
        """HTTP-side dependency mirroring the identity the MCP executor injects."""
        user = request.user
        if not isinstance(user, AuthenticatedIdentity):
            msg = "Authenticated identity is required for this endpoint"
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
            self,
            note_service: SQLSpecNoteService,
            resolved_user: AuthenticatedIdentity,
        ) -> list[Note]:
            rows = await note_service.list_for_owner(resolved_user.sub)
            return [msgspec.convert(note_row_to_public(row), Note) for row in rows]

        @post("/", opt={"mcp_tool": CREATE_NOTE_TOOL_NAME})
        async def create_note(
            self,
            data: dict[str, Any],
            note_service: SQLSpecNoteService,
            resolved_user: AuthenticatedIdentity,
        ) -> Note:
            payload = msgspec.convert(data, CreateNoteInput)
            row = await note_service.create(title=payload.title, body=payload.body, owner_sub=resolved_user.sub)
            return msgspec.convert(note_row_to_public(row), Note)

        @delete("/{note_id:str}", status_code=HTTP_200_OK, opt={"mcp_tool": DELETE_NOTE_TOOL_NAME})
        async def delete_note(
            self,
            note_id: str,
            note_service: SQLSpecNoteService,
            resolved_user: AuthenticatedIdentity,
        ) -> DeleteNoteResult:
            deleted = await note_service.delete_for_owner(note_id, resolved_user.sub)
            return DeleteNoteResult(deleted=deleted, note_id=note_id)

    @get("/notes/schema", opt={"mcp_resource": NOTES_SCHEMA_RESOURCE_NAME}, sync_to_thread=False)
    def notes_schema() -> NotesSchema:
        return NotesSchema()

    @get("/app/info", opt={"mcp_resource": APP_INFO_RESOURCE_NAME}, sync_to_thread=False)
    def get_api_info() -> AppInfo:
        return build_app_info(backend="sqlspec", auth_mode="jwt", supports_dishka=False)

    @asynccontextmanager
    async def mcp_dependency_provider(context: ToolExecutionContext) -> AsyncIterator[dict[str, Any]]:
        """Provide a fresh ``note_service`` per MCP tool call.

        The plugin's executor injects ``resolved_user`` directly from the
        validated claims returned by the shared ``user_resolver``, so this
        provider only needs to supply the SQLSpec-backed service. The
        static ``notes_schema`` / ``app_info`` resources take no extra
        kwargs, so they are skipped here.
        """
        opt = getattr(context.handler, "opt", {}) or {}
        if opt.get("mcp_tool") not in {LIST_NOTES_TOOL_NAME, CREATE_NOTE_TOOL_NAME, DELETE_NOTE_TOOL_NAME}:
            yield {}
            return
        async with provide_note_service(sqlspec, config) as service:
            yield {"note_service": service}

    async def on_startup() -> None:
        await bootstrap_schema(sqlspec, config)

    mcp_config = MCPConfig(dependency_provider=mcp_dependency_provider)
    mcp_config.auth = build_mcp_auth_config(secret=token_secret, issuer=issuer, audience=audience)

    return Litestar(
        route_handlers=[login_controller, NoteController, notes_schema, get_api_info],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), LitestarMCP(mcp_config)],
        on_app_init=[oauth_backend.on_app_init],
    )
