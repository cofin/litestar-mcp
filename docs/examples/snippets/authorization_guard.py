"""Authorize MCP tool dispatch with a Litestar Guard.

Scopes declared on ``@mcp_tool(scopes=[...])`` are discovery-only metadata
since v0.5.0. Attach a Guard to enforce access control — the same guard
protects HTTP and MCP paths.
"""

from typing import TYPE_CHECKING, Any

# start-example
from litestar import Controller, Litestar, get
from litestar.exceptions import PermissionDeniedException

from litestar_mcp import LitestarMCP

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler


_UNAUTHENTICATED = "Unauthenticated"


def requires_authenticated_user(
    connection: "ASGIConnection[Any, Any, Any, Any]",
    handler: "BaseRouteHandler",
) -> None:
    """Reject callers whose middleware did not populate ``scope['auth']``."""
    _ = handler
    if connection.scope.get("auth") is None:
        raise PermissionDeniedException(_UNAUTHENTICATED)


class ReportController(Controller):
    path = "/reports"
    guards = [requires_authenticated_user]

    @get("/", mcp_tool="generate_report", scopes=["report:read"], sync_to_thread=False)
    def generate_report(self) -> dict[str, str]:
        return {"status": "ok"}


app = Litestar(route_handlers=[ReportController], plugins=[LitestarMCP()])
# end-example
