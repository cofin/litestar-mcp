"""Snippet: authenticate MCP with the app's own Litestar middleware. Referenced from docs/usage/auth.rst."""

from typing import Any

from litestar import Litestar, get
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult, DefineMiddleware

from litestar_mcp import LitestarMCP

API_KEYS = {"let-me-in": {"sub": "demo-user", "roles": ["reader"]}}


# start-example
class ApiKeyAuthenticationMiddleware(AbstractAuthenticationMiddleware):
    """Populate ``request.user`` / ``request.auth`` from an ``X-API-Key`` header."""

    async def authenticate_request(self, connection: "ASGIConnection[Any, Any, Any, Any]") -> "AuthenticationResult":
        claims = API_KEYS.get(connection.headers.get("X-API-Key", ""))
        if claims is None:
            msg = "Invalid API key"
            raise NotAuthorizedException(msg)
        return AuthenticationResult(user=claims["sub"], auth=claims)


@get("/whoami", mcp_tool="whoami", sync_to_thread=False)
def whoami(request: "Any") -> "dict[str, Any]":
    return {"user": request.user, "roles": request.auth["roles"]}


app = Litestar(
    route_handlers=[whoami],
    middleware=[DefineMiddleware(ApiKeyAuthenticationMiddleware)],
    plugins=[LitestarMCP()],
)
# end-example
