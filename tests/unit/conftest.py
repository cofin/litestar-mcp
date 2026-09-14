"""Unit-test configuration and shared fixtures."""

import inspect
from typing import TYPE_CHECKING, Any

import pytest
from litestar import Litestar, get
from litestar.testing import AsyncTestClient, TestClient

from litestar_mcp import LitestarMCP

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler

pytestmark = pytest.mark.unit

PROTOCOL_VERSION = "2026-07-28"
NAME_FIELDS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
    "tasks/get": "taskId",
    "tasks/update": "taskId",
    "tasks/cancel": "taskId",
}


def mcp_envelope(
    method: "str",
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: "int" = 1,
    headers: "dict[str, str] | None" = None,
) -> "tuple[dict[str, Any], dict[str, str]]":
    """Build the JSON body and headers for one stateless MCP request.

    Args:
        method: JSON-RPC method name.
        params: Request parameters; envelope keys are merged under any ``_meta`` given.
        msg_id: JSON-RPC request id.
        headers: Extra headers merged under the MCP envelope headers.

    Returns:
        The ``(body, headers)`` pair to pass to ``client.post``.
    """
    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        **dict(request_params.get("_meta") or {}),
    }
    request_headers = {
        **(headers or {}),
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    name_field = NAME_FIELDS.get(method)
    if name_field is not None:
        request_headers["Mcp-Name"] = str(request_params.get(name_field, ""))
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": request_params}
    return body, request_headers


def mcp_post(
    client: "TestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: "int" = 1,
    headers: "dict[str, str] | None" = None,
    base: "str" = "/mcp",
) -> "Any":
    """POST one stateless MCP request and return the raw response."""
    body, request_headers = mcp_envelope(method, params, msg_id=msg_id, headers=headers)
    return client.post(base, json=body, headers=request_headers)


async def mcp_post_async(
    client: "AsyncTestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
    *,
    msg_id: "int" = 1,
    headers: "dict[str, str] | None" = None,
    base: "str" = "/mcp",
) -> "Any":
    """Async variant of :func:`mcp_post`."""
    body, request_headers = mcp_envelope(method, params, msg_id=msg_id, headers=headers)
    return await client.post(base, json=body, headers=request_headers)


@pytest.fixture
def minimal_app() -> "Litestar":
    """Minimal Litestar app without MCP plugin."""

    @get("/test", sync_to_thread=False)
    def test_route() -> "dict[str, str]":
        return {"message": "test"}

    return Litestar(route_handlers=[test_route])


@pytest.fixture
def mcp_app() -> "Litestar":
    """Litestar app with MCP plugin using opt pattern."""

    @get("/test", sync_to_thread=False)
    def test_route() -> "dict[str, str]":
        return {"message": "test"}

    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> "list[dict[str, Any]]":
        return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    @get("/config", opt={"mcp_resource": "app_config"}, sync_to_thread=False)
    def get_config() -> "dict[str, Any]":
        return {"debug": True, "version": "1.0.0"}

    return Litestar(route_handlers=[test_route, list_users, get_config], plugins=[LitestarMCP()])


@pytest.fixture
def client(mcp_app: "Litestar") -> "TestClient[Any]":
    """Test client for MCP-enabled app."""
    return TestClient(app=mcp_app)


def get_handler_from_app(app: "Litestar", path: "str", method: "str" = "GET") -> "BaseRouteHandler":
    """Extract a handler from an initialized Litestar app.

    Args:
        app: The Litestar app instance
        path: The route path to find
        method: HTTP method (default: GET)

    Returns:
        The BaseRouteHandler instance

    Raises:
        ValueError: If handler not found
    """
    for route in app.routes:
        if hasattr(route, "path") and route.path == path:
            if hasattr(route, "route_handlers"):
                for handler in route.route_handlers:  # pyright: ignore
                    if hasattr(handler, "http_methods") and method.upper() in handler.http_methods:  # pyright: ignore
                        return handler  # pyright: ignore
            elif hasattr(route, "route_handler"):
                handler = route.route_handler  # pyright: ignore
                if hasattr(handler, "http_methods") and method.upper() in handler.http_methods:  # pyright: ignore
                    return handler  # pyright: ignore

    msg = f"Handler not found for {method} {path}"
    raise ValueError(msg)


def create_app_with_handler(
    handler_func: "Any", route_path: "str" = "/test", method: "str" = "GET", **handler_kwargs: "Any"
) -> "tuple[Litestar, BaseRouteHandler]":
    """Create a Litestar app with a single handler and return both app and handler.

    Args:
        handler_func: The function to use as a handler
        route_path: The path for the route
        method: HTTP method (default: GET)
        **handler_kwargs: Additional kwargs to pass to the route decorator

    Returns:
        Tuple of (app, handler)
    """
    # Import here to avoid circular imports in tests
    from litestar import get, post

    # Choose the right decorator based on method
    decorator = get if method.upper() == "GET" else post

    # Apply the route decorator - only set sync_to_thread for sync functions
    decorator_kwargs = {**handler_kwargs}
    if not inspect.iscoroutinefunction(handler_func):
        decorator_kwargs["sync_to_thread"] = False
    decorated_handler = decorator(route_path, **decorator_kwargs)(handler_func)

    # Create app
    app = Litestar(route_handlers=[decorated_handler])

    # Extract the actual handler
    handler = get_handler_from_app(app, route_path, method)

    return app, handler
