"""Cloud Run-shaped SQLSpec reference notes example (app-managed JWT)."""

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

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

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

DEFAULT_USER_DIRECTORY: Final = {"alice": "alice-password", "bob": "bob-password"}
DEFAULT_SQLITE_URL: Final = "sqlite:///.reference-notes-sqlspec-cloud-run.sqlite"


@dataclass(frozen=True, slots=True)
class CloudRunSettings:
    """Env-driven settings for the Cloud Run reference app."""

    database_url: str
    jwt_signing_key: str
    jwt_issuer: str
    jwt_audience: str
    port: int
    log_level: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> CloudRunSettings:
        source = os.environ if env is None else env
        signing_key = source.get("JWT_SIGNING_KEY")
        if not signing_key:
            msg = "JWT_SIGNING_KEY must be set (Cloud Run deploys should use Secret Manager)."
            raise RuntimeError(msg)
        port_raw = source.get("PORT", "8080")
        try:
            port = int(port_raw)
        except ValueError as exc:  # pragma: no cover - defensive
            msg = f"PORT must be an integer, got {port_raw!r}"
            raise RuntimeError(msg) from exc
        return cls(
            database_url=source.get("DATABASE_URL", DEFAULT_SQLITE_URL),
            jwt_signing_key=signing_key,
            jwt_issuer=source.get("JWT_ISSUER", DEFAULT_ISSUER),
            jwt_audience=source.get("JWT_AUDIENCE", DEFAULT_AUDIENCE),
            port=port,
            log_level=source.get("LOG_LEVEL", "info"),
        )


def _database_path_from_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///:memory:"):
        return ":memory:"
    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///") :]
    if database_url.startswith("sqlite://"):
        return database_url[len("sqlite://") :]
    return database_url


def create_app(settings: CloudRunSettings | None = None) -> Litestar:
    """Create the Cloud Run-shaped, app-managed JWT reference app."""
    cfg = settings or CloudRunSettings.from_env()
    sqlite_path = _database_path_from_url(cfg.database_url)
    if sqlite_path != ":memory:":
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    sqlspec, config = build_sqlspec(sqlite_path)

    def _sign(sub: str) -> str:
        return mint_hs256_token(sub, secret=cfg.jwt_signing_key, issuer=cfg.jwt_issuer, audience=cfg.jwt_audience)

    oauth_backend = build_oauth_backend(
        secret=cfg.jwt_signing_key,
        issuer=cfg.jwt_issuer,
        audience=cfg.jwt_audience,
        exclude=["^/.well-known/", "^/healthz$", "^/auth/login"],
    )

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

    @get("/healthz", opt={"exclude_from_auth": True}, sync_to_thread=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def on_startup() -> None:
        await bootstrap_schema(sqlspec, config)

    mcp_config = MCPConfig(auth=build_mcp_auth_metadata(issuer=cfg.jwt_issuer, audience=cfg.jwt_audience))

    return Litestar(
        route_handlers=[
            build_login_controller(user_directory=DEFAULT_USER_DIRECTORY, token_signer=_sign),
            NoteController,
            notes_schema,
            get_api_info,
            healthz,
        ],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), LitestarMCP(mcp_config)],
        on_app_init=[oauth_backend.on_app_init],
    )


if __name__ == "__main__":  # pragma: no cover - manual smoke entry point
    import uvicorn

    _settings = CloudRunSettings.from_env()
    uvicorn.run(
        "docs.examples.notes.sqlspec.cloud_run_jwt:create_app",
        factory=True,
        host="0.0.0.0",
        port=_settings.port,
        log_level=_settings.log_level,
    )
