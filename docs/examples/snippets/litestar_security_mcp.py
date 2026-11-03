"""Snippet: MCP route_opt policy wiring, guard enforcement, and principal propagation."""

# start-example
from typing import TYPE_CHECKING, Any

from litestar import Litestar, Request, get
from litestar.exceptions import PermissionDeniedException

from litestar_mcp import LitestarMCP, MCPConfig

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler


def required(*mechanisms: str) -> dict[str, tuple[str, ...]]:
    """Build a route-level authentication mechanism requirement descriptor."""
    return {"required_mechanisms": mechanisms}


def require_notes_read_scope(
    connection: "ASGIConnection[Any, Any, Any, Any]",
    handler: "BaseRouteHandler",
) -> None:
    """Enforce that the caller's SecurityContext or auth claims include notes:read."""
    _ = handler
    security_context = connection.scope.get("litestar_security") or connection.scope.get("auth")
    if security_context is None:
        msg = "Authentication context is required"
        raise PermissionDeniedException(msg)
    authorization = getattr(security_context, "authorization", security_context)
    scopes = getattr(authorization, "scopes", None)
    if scopes is None and isinstance(authorization, dict):
        scopes = authorization.get("scopes", ())
    if "notes:read" not in (scopes or ()):
        msg = "Missing required scope: notes:read"
        raise PermissionDeniedException(msg)


@get("/notes/audit", mcp_tool="audit_notes", guards=[require_notes_read_scope])
async def audit_notes(request: Request[Any, Any, Any]) -> dict[str, Any]:
    """Return caller principal and active authorization scopes for audit verification."""
    principal = request.user
    subject = getattr(principal, "subject", None) or getattr(principal, "id", str(principal))
    security_context = request.scope.get("litestar_security") or request.auth
    authorization = getattr(security_context, "authorization", security_context)
    raw_scopes = getattr(authorization, "scopes", ())
    if not raw_scopes and isinstance(authorization, dict):
        raw_scopes = authorization.get("scopes", ())
    return {
        "subject": subject,
        "scopes": sorted(str(scope) for scope in raw_scopes),
    }


config = MCPConfig(
    route_opt={"auth": required("api-key", "bearer-jwt")},
    guards=[require_notes_read_scope],
)

app = Litestar(
    route_handlers=[audit_notes],
    plugins=[LitestarMCP(config)],
)
# end-example


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
