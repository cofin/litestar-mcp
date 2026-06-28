"""Integration tests for Google ADK MCP Toolset compatibility."""

import contextlib
import socket
import threading
import time
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
import uvicorn
from litestar import Litestar, get
from litestar.middleware import DefineMiddleware

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.auth import MCPAuthBackend, MCPAuthConfig
from litestar_mcp.utils import mcp_tool
from tests.integration._auth import (
    AUDIENCE,
    ISSUER,
    VALID_TOKEN,
    AuthenticatedUser,
    bearer_token_validator,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

google_adk = pytest.importorskip("google.adk")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.adk,
    pytest.mark.filterwarnings(
        r"ignore:\[EXPERIMENTAL\] feature (PLUGGABLE_AUTH|BASE_AUTHENTICATED_TOOL) is enabled\.:UserWarning:google\.adk\.features\._feature_decorator"
    ),
    pytest.mark.filterwarnings(
        r"ignore:\[EXPERIMENTAL\] feature MCP_GRACEFUL_ERROR_HANDLING is enabled\.:UserWarning:google\.adk\.tools\.mcp_tool\.mcp_toolset"
    ),
    pytest.mark.filterwarnings(
        r"ignore:Your application has authenticated using end user credentials from Google Cloud SDK without a quota project\..*:UserWarning:google\.auth\._default"
    ),
]


def _free_port() -> "int":
    """Find a free port on 127.0.0.1."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _run_app(app: "Litestar") -> "Iterator[str]":
    """Start the Litestar application in a daemon thread using uvicorn."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/.well-known/mcp-server.json", timeout=0.2)
            if resp.status_code == 200:
                yield base_url
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        err_msg = "test MCP server did not start"
        raise RuntimeError(err_msg)
    server.should_exit = True
    thread.join(timeout=5)


def _build_simple_app() -> "Litestar":
    """Build a minimal Litestar app for testing the harness."""
    return Litestar(route_handlers=[], plugins=[LitestarMCP()])


def _build_public_app() -> "Litestar":
    """Build a public Litestar app with an MCP tool handler."""

    @get("/hello/{name:str}", mcp_tool="hello", sync_to_thread=False)
    def hello(name: "str") -> "dict[str, str]":
        """Say hello to the given name."""
        return {"message": f"hello {name}"}

    return Litestar(route_handlers=[hello], plugins=[LitestarMCP()])


async def _user_resolver(claims: "dict[str, Any]", _app: "Any") -> "AuthenticatedUser":
    """Resolve the authenticated user from JWT claims."""
    scopes = claims.get("scopes") or []
    if not isinstance(scopes, list):
        scopes = []
    return AuthenticatedUser(sub=str(claims.get("sub", "")), scopes=tuple(str(s) for s in scopes))


def _build_auth_app() -> "Litestar":
    """Build an authenticated Litestar app with an MCP tool handler."""

    @get("/echo-user", sync_to_thread=False)
    @mcp_tool(name="echo_user")
    def echo_user(request: "Any") -> "dict[str, Any]":
        """Return the authenticated user's sub claim."""
        user = request.user
        return {"sub": getattr(user, "sub", None)}

    metadata = MCPAuthConfig(issuer=ISSUER, audience=AUDIENCE, scopes={"mcp:read": "Read MCP tools"})
    return Litestar(
        route_handlers=[echo_user],
        middleware=[
            DefineMiddleware(
                MCPAuthBackend,
                token_validator=bearer_token_validator,
                user_resolver=_user_resolver,
            ),
        ],
        plugins=[LitestarMCP(MCPConfig(auth=metadata))],
    )


def _build_resource_app() -> "Litestar":
    """Build a Litestar application exposing an MCP resource."""

    @get("/config", opt={"mcp_resource": "app_config"}, sync_to_thread=False)
    def config() -> "dict[str, bool]":
        """Return application configuration."""
        return {"debug": True}

    return Litestar(route_handlers=[config], plugins=[LitestarMCP()])


def test_harness_starts_and_stops_cleanly() -> "None":
    """Verify that the test harness can launch and terminate the server successfully."""
    app = _build_simple_app()
    with _run_app(app) as base_url:
        response = httpx.get(f"{base_url}/.well-known/mcp-server.json")
        assert response.status_code == 200
        assert "endpoints" in response.json()


@pytest.mark.asyncio
async def test_adk_mcp_toolset_discovers_and_calls_litestar_tool() -> "None":
    """Verify that ADK McpToolset can discover and invoke a tool exposed by Litestar."""
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    app = _build_public_app()
    with _run_app(app) as base_url:
        toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=f"{base_url}/mcp",
                headers={"Accept": "application/json, text/event-stream"},
            )
        )
        try:
            tools = await toolset.get_tools()
            tool = next(t for t in tools if t.name == "hello")
            result = await tool.run_async(args={"name": "Ada"}, tool_context=cast("Any", None))
            assert "Ada" in str(result)
        finally:
            await toolset.close()


@pytest.mark.asyncio
async def test_adk_mcp_toolset_auth_success() -> "None":
    """Verify that ADK McpToolset succeeds when a valid bearer token is supplied."""
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    app = _build_auth_app()
    with _run_app(app) as base_url:
        toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=f"{base_url}/mcp",
                headers={
                    "Authorization": f"Bearer {VALID_TOKEN}",
                    "Accept": "application/json, text/event-stream",
                },
            )
        )
        try:
            tools = await toolset.get_tools()
            tool = next(t for t in tools if t.name == "echo_user")
            result = await tool.run_async(args={}, tool_context=cast("Any", None))
            assert "integration-user" in str(result)
        finally:
            await toolset.close()


@pytest.mark.asyncio
async def test_adk_mcp_toolset_auth_failure() -> "None":
    """Verify that ADK McpToolset fails when no bearer token is supplied."""
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    app = _build_auth_app()
    with _run_app(app) as base_url:
        toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=f"{base_url}/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                },
            )
        )
        try:
            with pytest.raises(ConnectionError):
                await toolset.get_tools()
        finally:
            await toolset.close()


@pytest.mark.asyncio
async def test_adk_mcp_toolset_resources() -> "None":
    """Verify that ADK McpToolset can list and read resources exposed by Litestar."""
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    app = _build_resource_app()
    with _run_app(app) as base_url:
        toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=f"{base_url}/mcp",
                headers={"Accept": "application/json, text/event-stream"},
            )
        )
        try:
            resources = await toolset.list_resources()
            assert "app_config" in resources
            content = await toolset.read_resource("app_config")
            assert any("debug" in str(item) or "true" in str(item).lower() for item in content)
        finally:
            await toolset.close()
