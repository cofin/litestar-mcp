"""Google IAP-authenticated SQLSpec reference notes example.

This variant is the focused deployment-oriented example for putting the
shared notes MCP surface behind `Google Identity-Aware Proxy
<https://cloud.google.com/iap/docs>`_. Unlike :mod:`.jwt_auth`, the
application does not issue its own bearer tokens — IAP signs an
``x-goog-iap-jwt-assertion`` header on every forwarded request and the
backend validates it against Google's published JWKS before trusting the
identity.

Two pieces make this work end-to-end:

1. :func:`~docs.examples.notes.shared.auth.build_iap_token_validator`
   performs the ``ES256`` signature check, audience match, and issuer
   match. It is wired into :class:`~litestar_mcp.auth.MCPAuthConfig` so
   the MCP plugin rejects unsigned or invalid requests with ``401``.
2. :func:`~docs.examples.notes.shared.auth.build_iap_header_alias_middleware`
   aliases ``x-goog-iap-jwt-assertion`` into ``Authorization: Bearer
   <token>`` at the ASGI layer, so the existing MCP bearer-token boundary
   can consume the IAP assertion without a parallel auth code path.

The convenience identity headers (``x-goog-authenticated-user-email``,
``x-goog-authenticated-user-id``) are intentionally ignored. They are
not signed and must never drive security decisions.

Deployment
----------

On Cloud Run, ``audience`` must be set to the IAP-published value for the
service::

    /projects/<PROJECT_NUMBER>/global/backendServices/<SERVICE_ID>

See `Getting the signed header JWT audience
<https://cloud.google.com/iap/docs/signed-headers-howto#verify_the_jwt_payload>`_.

``jwks_url`` defaults to Google's public IAP JWKS endpoint; the JWKS
document is cached for one hour via the same helper used by the core
``litestar_mcp.auth`` OIDC path.
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

from docs.examples.notes.shared.auth import (
    DEFAULT_IAP_ISSUER,
    DEFAULT_IAP_JWKS_URL,
    AuthenticatedIdentity,
    build_iap_header_alias_middleware,
    build_iap_mcp_auth_config,
    identity_from_claims,
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


def create_app(
    database_path: str | None = None,
    *,
    audience: str,
    issuer: str = DEFAULT_IAP_ISSUER,
    jwks_url: str = DEFAULT_IAP_JWKS_URL,
) -> Litestar:
    """Create the Google IAP-protected SQLSpec reference notes app.

    Args:
        database_path: Optional SQLite file path. When omitted, a
            ``.reference-notes-sqlspec-google-iap.sqlite`` file in the
            current working directory is used. Deployments typically
            override this to point at a managed database such as Cloud
            SQL; the example keeps SQLite as the runnable-out-of-the-box
            shape.
        audience: The IAP-published audience for the Cloud Run backend
            service (``/projects/<PROJECT_NUMBER>/global/backendServices/<SERVICE_ID>``).
            No default — deployments MUST configure this explicitly so
            tokens intended for a different service are rejected.
        issuer: Expected ``iss`` claim. Defaults to
            :data:`~docs.examples.notes.shared.auth.DEFAULT_IAP_ISSUER`.
        jwks_url: Google IAP JWKS endpoint. Defaults to the public
            Google-hosted URL; tests can override to a local fixture.
    """
    sqlite_path = Path(database_path or Path.cwd() / ".reference-notes-sqlspec-google-iap.sqlite")
    sqlspec, config = build_sqlspec(str(sqlite_path))

    async def _provide_resolved_user_from_headers(request: Any) -> AuthenticatedIdentity:
        """Derive the identity from the IAP-aliased ``Authorization`` header.

        HTTP handlers fall back to this when Litestar is not running an
        OAuth backend (the IAP example intentionally avoids one — the
        signed header is the sole trust root). The MCP executor receives
        the identity through ``user_resolver`` and does not need this
        dependency.
        """
        from litestar.exceptions import NotAuthorizedException

        from docs.examples.notes.shared.auth import build_iap_token_validator

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            msg = "Missing IAP assertion"
            raise NotAuthorizedException(msg)
        token = auth_header[7:]
        validator = build_iap_token_validator(audience=audience, issuer=issuer, jwks_url=jwks_url)
        claims = await validator(token)
        if claims is None:
            msg = "Invalid IAP assertion"
            raise NotAuthorizedException(msg)
        return identity_from_claims(claims)

    async def note_service_provider() -> AsyncIterator[SQLSpecNoteService]:
        async with provide_note_service(sqlspec, config) as service:
            yield service

    class NoteController(Controller):
        path = "/notes"
        dependencies = {
            "note_service": Provide(note_service_provider),
            "resolved_user": Provide(_provide_resolved_user_from_headers),
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
        return build_app_info(backend="sqlspec", auth_mode="google_iap", supports_dishka=False)

    @asynccontextmanager
    async def mcp_dependency_provider(context: ToolExecutionContext) -> AsyncIterator[dict[str, Any]]:
        """Provide a fresh SQLSpec-backed ``note_service`` per MCP tool call."""
        opt = getattr(context.handler, "opt", {}) or {}
        if opt.get("mcp_tool") not in {
            LIST_NOTES_TOOL_NAME,
            CREATE_NOTE_TOOL_NAME,
            DELETE_NOTE_TOOL_NAME,
        }:
            yield {}
            return
        async with provide_note_service(sqlspec, config) as service:
            yield {"note_service": service}

    async def on_startup() -> None:
        await bootstrap_schema(sqlspec, config)

    mcp_config = MCPConfig(dependency_provider=mcp_dependency_provider)
    mcp_config.auth = build_iap_mcp_auth_config(audience=audience, issuer=issuer, jwks_url=jwks_url)

    return Litestar(
        route_handlers=[NoteController, notes_schema, get_api_info],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), LitestarMCP(mcp_config)],
        middleware=[build_iap_header_alias_middleware],
    )
