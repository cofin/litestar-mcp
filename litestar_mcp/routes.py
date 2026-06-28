# ruff: noqa: PLR0911
"""MCP JSON-RPC 2.0 Streamable HTTP transport for Litestar applications."""

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from litestar import Controller, Litestar, MediaType, Request, Response, delete, get, post
from litestar.di import NamedDependency  # noqa: TC002
from litestar.exceptions import SerializationException
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.serialization import decode_json
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_405_METHOD_NOT_ALLOWED,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from litestar_mcp.config import MCPConfig  # noqa: TC001
from litestar_mcp.jsonrpc import (
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRouter,
    error_response,
    parse_request,
)
from litestar_mcp.registry import PromptRegistration, Registry  # noqa: TC001
from litestar_mcp.services.handler import MCPHandlerService, RequestContext
from litestar_mcp.sessions import (
    MCPSessionManager,
    SessionMissingError,
    SessionNotInitializedError,
    SessionTerminated,
)
from litestar_mcp.sse import StreamLimitExceeded
from litestar_mcp.tasks import InMemoryTaskStore  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_SESSION_HEADER = "Mcp-Session-Id"

SESSION_ERROR = -32000
SESSION_NOT_INITIALIZED = -32002


def _validate_origin(request: "Request[Any, Any, Any]", config: "MCPConfig") -> "Response[Any] | None":
    """Validate the Origin header if allowed_origins is configured."""
    if not config.allowed_origins:
        return None

    origin = request.headers.get("origin")
    if origin and origin not in config.allowed_origins:
        return Response(
            content={"error": "Origin not allowed"},
            status_code=HTTP_403_FORBIDDEN,
            media_type=MediaType.JSON,
        )
    return None


def _add_protocol_headers(response: "Response[Any]") -> "Response[Any]":
    """Add standard MCP protocol headers to a response."""
    response.headers["mcp-protocol-version"] = MCP_PROTOCOL_VERSION
    return response


def _request_subject(request: "Request[Any, Any, Any]") -> "str | None":
    """Best-effort ``sub``-like identifier from ``request.auth`` claims dict.

    Middleware populates ``scope["auth"]`` with whatever shape it sets — this
    helper reads the raw scope value (avoiding the ``request.auth`` property
    which raises when no auth middleware is installed) and treats it as a
    mapping, pulling ``"sub"`` if present. Non-mapping values are ignored.
    """
    auth = request.scope.get("auth")
    if isinstance(auth, dict):
        sub = auth.get("sub")
        if isinstance(sub, str) and sub:
            return sub
    return None


def _resolve_client_id(request: "Request[Any, Any, Any]") -> "str":
    explicit_client_id = (
        request.headers.get("x-mcp-client-id")
        or request.headers.get("mcp-client-id")
        or request.query_params.get("clientId")
        or request.query_params.get("client_id")
    )
    if explicit_client_id:
        return explicit_client_id
    sub = _request_subject(request)
    if sub is not None:
        return f"user:{sub}"
    if request.client and request.client.host:
        return f"remote:{request.client.host}"
    return "anonymous"


def _build_request_context(request: "Request[Any, Any, Any]") -> "RequestContext":
    client_id = _resolve_client_id(request)
    sub = _request_subject(request)
    owner_id = f"user:{sub}" if sub is not None else f"client:{client_id}"
    return RequestContext(client_id=client_id, owner_id=owner_id, request=request)


def _build_cached_router(
    app: "Litestar",
    config: "MCPConfig",
    discovered_tools: "dict[str, Any]",
    discovered_resources: "dict[str, Any]",
    discovered_prompts: "dict[str, PromptRegistration]",
    registry: "Registry",
    task_store: "InMemoryTaskStore | None",
) -> "JSONRPCRouter":
    """Build and register handlers on a new JSONRPCRouter instance."""
    router = JSONRPCRouter()

    def make_handler_service() -> "MCPHandlerService":
        return MCPHandlerService(
            config=config,
            discovered_tools=discovered_tools,
            discovered_resources=discovered_resources,
            discovered_prompts=discovered_prompts,
            app_ref=app,
            registry=registry,
            task_store=task_store,
        )

    router.register("initialize", lambda params, ctx: make_handler_service().initialize(params, ctx))
    router.register("notifications/initialized", lambda params, ctx: make_handler_service().initialized(params, ctx))
    router.register("ping", lambda params, ctx: make_handler_service().ping(params, ctx))
    router.register("tools/list", lambda params, ctx: make_handler_service().tools_list(params, ctx))
    router.register("tools/call", lambda params, ctx: make_handler_service().tools_call(params, ctx))
    router.register("resources/list", lambda params, ctx: make_handler_service().resources_list(params, ctx))
    router.register(
        "resources/templates/list", lambda params, ctx: make_handler_service().resources_templates_list(params, ctx)
    )
    router.register("resources/read", lambda params, ctx: make_handler_service().resources_read(params, ctx))
    router.register("completion/complete", lambda params, ctx: make_handler_service().completion_complete(params, ctx))
    router.register("prompts/list", lambda params, ctx: make_handler_service().prompts_list(params, ctx))
    router.register("prompts/get", lambda params, ctx: make_handler_service().prompts_get(params, ctx))

    if task_store is not None and config.task_config is not None and config.task_config.enabled:
        router.register("tasks/get", lambda params, ctx: make_handler_service().tasks_get(params, ctx))
        router.register("tasks/result", lambda params, ctx: make_handler_service().tasks_result(params, ctx))
        router.register("tasks/list", lambda params, ctx: make_handler_service().tasks_list(params, ctx))
        router.register("tasks/cancel", lambda params, ctx: make_handler_service().tasks_cancel(params, ctx))

    return router


class MCPController(Controller):
    """MCP JSON-RPC 2.0 Streamable HTTP controller."""

    @get("/", name="mcp_sse", media_type=MediaType.TEXT)
    async def handle_sse(
        self,
        request: "Request[Any, Any, Any]",
        config: "NamedDependency[MCPConfig]",
        registry: "NamedDependency[Registry]",
        session_manager: "NamedDependency[MCPSessionManager]",
    ) -> "Response[Any]":
        """Handle GET-based Streamable HTTP SSE streams on the MCP endpoint."""
        origin_err = _validate_origin(request, config)
        if origin_err is not None:
            return origin_err

        accept_header = request.headers.get("accept", "")
        if "text/event-stream" not in accept_header:
            return _add_protocol_headers(
                Response(
                    content={"error": "GET /mcp requires Accept: text/event-stream"},
                    status_code=HTTP_405_METHOD_NOT_ALLOWED,
                    media_type=MediaType.JSON,
                )
            )

        session_id = request.headers.get(MCP_SESSION_HEADER) or request.headers.get(MCP_SESSION_HEADER.lower())
        if not session_id:
            return _add_protocol_headers(
                Response(
                    content={"error": f"Missing required header: {MCP_SESSION_HEADER}"},
                    status_code=HTTP_400_BAD_REQUEST,
                    media_type=MediaType.JSON,
                )
            )
        try:
            await session_manager.get(session_id)
        except SessionTerminated:
            return _add_protocol_headers(
                Response(
                    content=error_response(
                        None, JSONRPCError(code=SESSION_ERROR, message="Session terminated or unknown")
                    ),
                    status_code=HTTP_404_NOT_FOUND,
                    media_type=MediaType.JSON,
                )
            )

        try:
            stream_id, stream = await registry.sse_manager.open_stream(
                session_id=session_id,
                last_event_id=request.headers.get("last-event-id"),
            )
        except StreamLimitExceeded:
            return _add_protocol_headers(
                Response(
                    content=error_response(None, JSONRPCError(code=SESSION_ERROR, message="SSE stream limit exceeded")),
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    media_type=MediaType.JSON,
                )
            )

        async def event_stream() -> "AsyncGenerator[ServerSentEventMessage, None]":
            try:
                async for message in stream:
                    yield ServerSentEventMessage(data=message.data, event=message.event, id=message.id)
            finally:
                registry.sse_manager.disconnect(stream_id)

        response = ServerSentEvent(event_stream())
        response.headers[MCP_SESSION_HEADER] = session_id
        return _add_protocol_headers(response)

    @delete("/", name="mcp_session_delete", status_code=HTTP_200_OK)
    async def handle_delete(
        self,
        request: "Request[Any, Any, Any]",
        config: "NamedDependency[MCPConfig]",
        registry: "NamedDependency[Registry]",
        session_manager: "NamedDependency[MCPSessionManager]",
    ) -> "Response[Any]":
        """Terminate an MCP session and close its attached SSE streams."""
        origin_err = _validate_origin(request, config)
        if origin_err is not None:
            return origin_err

        session_id = request.headers.get(MCP_SESSION_HEADER) or request.headers.get(MCP_SESSION_HEADER.lower())
        if not session_id:
            return _add_protocol_headers(
                Response(
                    content={"error": f"Missing required header: {MCP_SESSION_HEADER}"},
                    status_code=HTTP_400_BAD_REQUEST,
                    media_type=MediaType.JSON,
                )
            )

        registry.sse_manager.close_session_streams(session_id)
        await session_manager.delete(session_id)
        return _add_protocol_headers(Response(content=None, status_code=HTTP_204_NO_CONTENT))

    @post("/", name="mcp_jsonrpc", media_type=MediaType.JSON, status_code=HTTP_200_OK)
    async def handle_jsonrpc(
        self,
        request: "Request[Any, Any, Any]",
        config: "NamedDependency[MCPConfig]",
        discovered_tools: "NamedDependency[dict[str, Any]]",
        discovered_resources: "NamedDependency[dict[str, Any]]",
        discovered_prompts: "NamedDependency[dict[str, PromptRegistration]]",
        registry: "NamedDependency[Registry]",
        session_manager: "NamedDependency[MCPSessionManager]",
        task_store: "NamedDependency[InMemoryTaskStore | None]" = None,
    ) -> "Response[Any]":
        """Handle a JSON-RPC 2.0 request over Streamable HTTP."""
        origin_err = _validate_origin(request, config)
        if origin_err is not None:
            return origin_err

        try:
            raw = decode_json(await request.body())
        except (SerializationException, ValueError):
            return _add_protocol_headers(
                Response(
                    content=error_response(None, JSONRPCError(code=PARSE_ERROR, message="Parse error")),
                    status_code=HTTP_200_OK,
                    media_type=MediaType.JSON,
                )
            )

        try:
            rpc_request = parse_request(raw)
        except JSONRPCErrorException as exc:
            return _add_protocol_headers(
                Response(
                    content=error_response(raw.get("id") if isinstance(raw, dict) else None, exc.error),
                    status_code=HTTP_200_OK,
                    media_type=MediaType.JSON,
                )
            )

        incoming_session_id = request.headers.get(MCP_SESSION_HEADER) or request.headers.get(MCP_SESSION_HEADER.lower())
        session = None
        response_session_id: str | None = None

        if rpc_request.method == "initialize":
            params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
            session = await session_manager.create(
                protocol_version=params.get("protocolVersion", MCP_PROTOCOL_VERSION),
                client_info=params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else None,
                capabilities=params.get("capabilities") if isinstance(params.get("capabilities"), dict) else None,
            )
            response_session_id = session.id
        else:
            try:
                session = await session_manager.validate_session(incoming_session_id, rpc_request.method)
                if session:
                    response_session_id = session.id
            except SessionMissingError:
                return _add_protocol_headers(
                    Response(
                        content=error_response(
                            rpc_request.id,
                            JSONRPCError(code=SESSION_ERROR, message=f"Missing required header: {MCP_SESSION_HEADER}"),
                        ),
                        status_code=HTTP_400_BAD_REQUEST,
                        media_type=MediaType.JSON,
                    )
                )
            except SessionTerminated:
                return _add_protocol_headers(
                    Response(
                        content=error_response(
                            rpc_request.id,
                            JSONRPCError(code=SESSION_ERROR, message="Session terminated or unknown"),
                        ),
                        status_code=HTTP_404_NOT_FOUND,
                        media_type=MediaType.JSON,
                    )
                )
            except SessionNotInitializedError:
                return _add_protocol_headers(
                    Response(
                        content=error_response(
                            rpc_request.id,
                            JSONRPCError(code=SESSION_NOT_INITIALIZED, message="Session not initialized"),
                        ),
                        status_code=HTTP_200_OK,
                        media_type=MediaType.JSON,
                    )
                )

        if rpc_request.method == "notifications/initialized" and incoming_session_id:
            with contextlib.suppress(SessionTerminated):
                await session_manager.mark_initialized(incoming_session_id)

        request_context = _build_request_context(request)
        app = request.app
        if not hasattr(app.state, "mcp_router"):
            app.state.mcp_router = _build_cached_router(
                app=app,
                config=config,
                discovered_tools=discovered_tools,
                discovered_resources=discovered_resources,
                discovered_prompts=discovered_prompts,
                registry=registry,
                task_store=task_store,
            )
        router = app.state.mcp_router

        result = await router.dispatch(rpc_request, request_context)

        response: Response[Any]
        if result is None:
            response = Response(content=b"", status_code=HTTP_202_ACCEPTED)
        else:
            response = Response(content=result, status_code=HTTP_200_OK, media_type=MediaType.JSON)

        if response_session_id is not None:
            response.headers[MCP_SESSION_HEADER] = response_session_id

        return _add_protocol_headers(response)
