# ruff: noqa: PLR0911
"""MCP Streamable HTTP JSON-RPC and SSE transport for Litestar applications."""

import asyncio
import base64
import binascii
import contextlib
import inspect
from typing import TYPE_CHECKING, Any, Literal

import anyio
from litestar import Controller, Litestar, MediaType, Request, Response, delete, get, post
from litestar.di import NamedDependency  # noqa: TC002
from litestar.exceptions import SerializationException
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_405_METHOD_NOT_ALLOWED,
    HTTP_406_NOT_ACCEPTABLE,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from litestar_mcp.core._streaming import StreamCleanupMiddleware, start_stream
from litestar_mcp.core.jsonrpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRouter,
    error_response,
    parse_request,
)
from litestar_mcp.core.schema_builder import cached_schema_for_handler, iter_mcp_header_fields
from litestar_mcp.core.serialization import from_json, to_json
from litestar_mcp.core.sse import LOG_LEVEL_SEVERITY, StreamLimitExceeded, log_level_allows
from litestar_mcp.mcp.config import SUPPORTED_PROTOCOL_VERSIONS, MCPConfig
from litestar_mcp.mcp.registry import PromptRegistration, Registry, evaluate_subscription_authorizer
from litestar_mcp.mcp.service import MCPHandlerService, MCPRequestContext, get_session_store
from litestar_mcp.mcp.skills import SkillCatalog  # noqa: TC001
from litestar_mcp.mcp.tasks import MCPTaskStore  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from anyio.streams.memory import MemoryObjectSendStream

    from litestar_mcp.core.jsonrpc import JSONRPCRequest
    from litestar_mcp.mcp.service import MCPSession

MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_FALLBACK_SESSION_VERSION = "2025-11-25"
MCP_PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
MCP_SESSION_ID_HEADER = "Mcp-Session-Id"
MCP_METHOD_HEADER = "Mcp-Method"
MCP_NAME_HEADER = "Mcp-Name"
MCP_OWNER_ID_SCOPE_KEY = "litestar_mcp.owner_id"

HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022

MCP_NAME_FIELDS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
    "tasks/get": "taskId",
    "tasks/update": "taskId",
    "tasks/cancel": "taskId",
    "skills/get": "uri",
    "resources/directory/read": "uri",
}
_MODERN_DISALLOWED_METHODS = frozenset(
    {"initialize", "ping", "resources/subscribe", "resources/unsubscribe", "logging/setLevel"}
)
_CACHEABLE_METHODS = {
    "server/discover",
    "tools/list",
    "resources/list",
    "resources/templates/list",
    "resources/read",
    "prompts/list",
    "skills/list",
    "skills/get",
}
_BASE64_PREFIX = "=?base64?"
_BASE64_SUFFIX = "?="
_MIN_VISIBLE_ASCII = 0x20
_MAX_VISIBLE_ASCII = 0x7E


def encode_mcp_header_value(value: "str") -> "str":
    """Encode header value with the MCP ``=?base64?...`` sentinel when non-visible-ASCII or colliding."""
    if (
        value
        and all(_MIN_VISIBLE_ASCII <= ord(char) <= _MAX_VISIBLE_ASCII for char in value)
        and not value.startswith(_BASE64_PREFIX)
    ):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"{_BASE64_PREFIX}{encoded}{_BASE64_SUFFIX}"


def decode_mcp_header_value(value: "str") -> "str":
    """Decode the MCP Base64 sentinel encoding, returning plain values unchanged."""
    if not (value.startswith(_BASE64_PREFIX) and value.endswith(_BASE64_SUFFIX)):
        return value
    payload = value[len(_BASE64_PREFIX) : -len(_BASE64_SUFFIX)]
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        msg = "Invalid MCP Base64 header value"
        raise ValueError(msg) from exc


class MCPController(Controller):
    """MCP JSON-RPC Streamable HTTP controller."""

    @get("/", name="mcp_get", status_code=HTTP_200_OK)
    async def stream_events(
        self,
        request: "Request[Any, Any, Any]",
        config: "NamedDependency[MCPConfig]",
        registry: "NamedDependency[Registry]",
    ) -> "Response[Any]":
        """Open a server-initiated SSE notification stream for pre-`2026-07-28` MCP clients."""
        origin_error = _validate_origin(request, config)
        if origin_error is not None:
            return origin_error
        header_version = _header_protocol_version(request)
        if not config.enable_get_sse or header_version == MCP_PROTOCOL_VERSION:
            return _method_not_allowed()
        accept_mode = _accepts_sse(request.headers.get("accept"))
        if accept_mode == "missing_or_wildcard":
            ver = _supported_header_version(header_version) or MCP_PROTOCOL_VERSION
            return _method_not_allowed(protocol_version=ver)
        if accept_mode == "incompatible":
            return _error(
                None,
                code=INVALID_REQUEST,
                message="Not Acceptable: Accept header must include text/event-stream",
                status_code=HTTP_406_NOT_ACCEPTABLE,
                protocol_version=_supported_header_version(header_version) or MCP_FALLBACK_SESSION_VERSION,
            )
        if header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return _unsupported_version_error(None, header_version)

        owner_id = _request_owner_id(request)
        session: MCPSession | None = None
        raw_session_id = request.headers.get(MCP_SESSION_ID_HEADER)
        if raw_session_id is not None:
            session = await get_session_store(request.app, config).get(raw_session_id.strip(), owner_id)
            if session is None:
                return _session_not_found_error(
                    None,
                    protocol_version=header_version or MCP_FALLBACK_SESSION_VERSION,
                )

        _sync_session_logging(registry, config, session)

        notifications = (
            {"resourceSubscriptions": list(session.subscriptions)}
            if session is not None and session.subscriptions
            else None
        )
        fallback_version = session.protocol_version if session is not None else MCP_FALLBACK_SESSION_VERSION
        resolved_version = header_version or fallback_version
        try:
            stream_id, stream = await registry.subscription_manager.open_session_stream(
                session_id=session.session_id if session is not None else None,
                owner_id=owner_id,
                notifications=notifications,
                authorize=_make_subscription_authorizer(request, config),
            )
        except StreamLimitExceeded as exc:
            return _error(
                None,
                code=INVALID_PARAMS,
                message=str(exc),
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                protocol_version=resolved_version,
            )
        return _stream_subscription_events(stream_id, stream, registry, config, protocol_version=resolved_version)

    @delete(
        "/",
        name="mcp_delete",
        status_code=HTTP_200_OK,
        opt={"exclude_from_csrf": True},
    )
    async def terminate_session(
        self,
        request: "Request[Any, Any, Any]",
        config: "NamedDependency[MCPConfig]",
        registry: "NamedDependency[Registry]",
    ) -> "Response[Any]":
        """Terminate an active `2025-*` MCP session or acknowledge stateless termination."""
        origin_error = _validate_origin(request, config)
        if origin_error is not None:
            return origin_error
        header_version = _header_protocol_version(request)
        if header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return _unsupported_version_error(None, header_version)
        if header_version == MCP_PROTOCOL_VERSION:
            response: Response[Any] = Response(content=b"", status_code=HTTP_204_NO_CONTENT, media_type=MediaType.TEXT)
            response.headers[MCP_PROTOCOL_VERSION_HEADER] = MCP_PROTOCOL_VERSION
            return response

        raw_session_id = request.headers.get(MCP_SESSION_ID_HEADER)
        if raw_session_id is None:
            response = Response(content=b"", status_code=HTTP_204_NO_CONTENT, media_type=MediaType.TEXT)
            response.headers[MCP_PROTOCOL_VERSION_HEADER] = header_version or MCP_PROTOCOL_VERSION
            return response

        sid = raw_session_id.strip()
        owner_id = _request_owner_id(request)
        session_store = get_session_store(request.app, config)
        session = await session_store.get(sid, owner_id)
        if session is None or not await session_store.delete(sid, owner_id):
            return _session_not_found_error(None, protocol_version=header_version or MCP_PROTOCOL_VERSION)

        await _close_registry_session_streams(registry, sid)
        response = Response(content=b"", status_code=HTTP_204_NO_CONTENT, media_type=MediaType.TEXT)
        response.headers[MCP_PROTOCOL_VERSION_HEADER] = header_version or session.protocol_version
        return response

    @post("/", name="mcp_jsonrpc", status_code=HTTP_200_OK, middleware=[StreamCleanupMiddleware()])
    async def handle_jsonrpc(
        self,
        request: "Request[Any, Any, Any]",
        config: "NamedDependency[MCPConfig]",
        discovered_tools: "NamedDependency[dict[str, Any]]",
        discovered_resources: "NamedDependency[dict[str, Any]]",
        discovered_prompts: "NamedDependency[dict[str, PromptRegistration]]",
        registry: "NamedDependency[Registry]",
        task_store: "NamedDependency[MCPTaskStore | None]" = None,
        skill_catalog: "NamedDependency[SkillCatalog | None]" = None,
    ) -> "Response[Any]":
        """Validate and dispatch one MCP JSON-RPC request or notification."""
        origin_error = _validate_origin(request, config)
        if origin_error is not None:
            return origin_error
        try:
            raw = from_json(await request.body())
        except (SerializationException, ValueError):
            return _error(None, code=PARSE_ERROR, message="Parse error", status_code=HTTP_400_BAD_REQUEST)

        header_version = _header_protocol_version(request)
        if isinstance(raw, dict):
            raw_method = raw.get("method")
            if (
                raw.get("jsonrpc") == "2.0"
                and "id" not in raw
                and isinstance(raw_method, str)
                and raw_method.startswith("notifications/")
            ):
                return await _handle_client_notification(request, config, header_version)
            request_id = raw.get("id")
            if not (
                isinstance(request_id, str)
                or type(request_id) is int
                or (isinstance(request_id, float) and request_id.is_integer())
            ):
                return _error(
                    None,
                    code=INVALID_REQUEST,
                    message="MCP request id must be a string or integer",
                    status_code=HTTP_400_BAD_REQUEST,
                )

        try:
            rpc_request = parse_request(raw)
        except JSONRPCErrorException as exc:
            return _error(
                raw.get("id") if isinstance(raw, dict) else None,
                code=exc.error.code,
                message=exc.error.message,
                data=exc.error.data,
                status_code=HTTP_400_BAD_REQUEST,
            )

        metadata_error = _request_metadata_error(request, rpc_request, discovered_tools)
        if metadata_error is not None:
            return metadata_error

        is_modern_2026 = _is_modern_2026_rpc(request, rpc_request)
        session, session_error = await _resolve_post_session(
            request,
            rpc_request,
            config,
            header_version=header_version,
            is_modern_2026=is_modern_2026,
        )
        if session_error is not None:
            return session_error
        _sync_session_logging(registry, config, session)
        if rpc_request.method == "subscriptions/listen":
            return await _subscription_response(request, rpc_request, registry, config)

        router = _ensure_router(
            request.app,
            config,
            discovered_tools,
            discovered_resources,
            discovered_prompts,
            registry,
            task_store,
            skill_catalog,
        )
        if rpc_request.method not in router.methods:
            fallback_err_version = session.protocol_version if session is not None else MCP_PROTOCOL_VERSION
            err_version = MCP_PROTOCOL_VERSION if is_modern_2026 else (header_version or fallback_err_version)
            return _error(
                rpc_request.id,
                code=METHOD_NOT_FOUND,
                message=f"Method not found: {rpc_request.method}",
                status_code=HTTP_404_NOT_FOUND,
                protocol_version=err_version,
            )
        if _progress_token(rpc_request) is not None and rpc_request.method != "initialize":
            return _progress_response(request, rpc_request, router, config, registry, session=session)
        return await _dispatch_jsonrpc_response(
            request,
            rpc_request,
            router,
            config,
            registry,
            is_modern_2026=is_modern_2026,
            session=session,
        )


def _error(
    request_id: "Any",
    *,
    code: "int",
    message: "str",
    status_code: "int",
    data: "Any | None" = None,
    protocol_version: "str" = MCP_PROTOCOL_VERSION,
) -> "Response[Any]":
    response = Response(
        content=error_response(request_id, JSONRPCError(code=code, message=message, data=data)),
        status_code=status_code,
        media_type=MediaType.JSON,
    )
    response.headers[MCP_PROTOCOL_VERSION_HEADER] = protocol_version
    return response


def _unsupported_version_error(request_id: "Any", requested: "str") -> "Response[Any]":
    return _error(
        request_id,
        code=UNSUPPORTED_PROTOCOL_VERSION,
        message=f"Unsupported protocol version: {requested}",
        status_code=HTTP_400_BAD_REQUEST,
        data={
            "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "requested": requested,
        },
    )


def _session_not_found_error(
    request_id: "Any",
    *,
    protocol_version: "str" = MCP_PROTOCOL_VERSION,
) -> "Response[Any]":
    return _error(
        request_id,
        code=INVALID_PARAMS,
        message="Session not found or expired",
        status_code=HTTP_404_NOT_FOUND,
        protocol_version=protocol_version,
    )


def _method_not_allowed(*, protocol_version: "str" = MCP_PROTOCOL_VERSION) -> "Response[Any]":
    response = _error(
        None,
        code=INVALID_REQUEST,
        message="Method Not Allowed",
        status_code=HTTP_405_METHOD_NOT_ALLOWED,
        protocol_version=protocol_version,
    )
    response.headers["Allow"] = "POST, DELETE"
    return response


def _header_protocol_version(request: "Request[Any, Any, Any]") -> "str | None":
    raw = request.headers.get(MCP_PROTOCOL_VERSION_HEADER)
    return raw.strip() if raw is not None else None


def _supported_header_version(header_version: "str | None") -> "str | None":
    return header_version if header_version is not None and header_version in SUPPORTED_PROTOCOL_VERSIONS else None


def _accepts_sse(accept_header: "str | None") -> "Literal['sse', 'missing_or_wildcard', 'incompatible']":
    """Classify an HTTP `Accept` header for `GET` SSE negotiation."""
    if accept_header is None or not accept_header.strip():
        return "missing_or_wildcard"
    saw_non_wildcard = False
    saw_rejected_sse = False
    for raw_item in accept_header.split(","):
        item = raw_item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split(";")]
        media_range = parts[0].lower()
        quality = 1.0
        q_invalid = False
        for param in parts[1:]:
            if "=" not in param:
                continue
            key, raw_val = param.split("=", 1)
            if key.strip().lower() == "q":
                try:
                    quality = float(raw_val.strip())
                except ValueError:
                    q_invalid = True
                break
        if q_invalid or not (0.0 <= quality <= 1.0):
            saw_non_wildcard = True
            continue
        if media_range == "text/event-stream":
            if quality > 0.0:
                return "sse"
            saw_rejected_sse = True
        elif media_range != "*/*" or quality == 0.0:
            saw_non_wildcard = True
    if saw_rejected_sse or saw_non_wildcard:
        return "incompatible"
    return "missing_or_wildcard"


def _request_origin(request: "Request[Any, Any, Any]") -> "str":
    return f"{request.url.scheme}://{request.url.netloc}"


def _validate_origin(request: "Request[Any, Any, Any]", config: "MCPConfig") -> "Response[Any] | None":
    """Reject a present Origin unless it is same-origin or explicitly allowed."""
    origin = request.headers.get("origin")
    if origin is None:
        return None
    allowed = set(config.allowed_origins or ())
    allowed.add(_request_origin(request))
    if origin in allowed:
        return None
    return _error(
        None,
        code=INVALID_PARAMS,
        message="Origin not allowed",
        status_code=HTTP_403_FORBIDDEN,
    )


def _is_modern_2026_rpc(request: "Request[Any, Any, Any]", rpc_request: "JSONRPCRequest") -> "bool":
    params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
    meta = params.get("_meta")
    header_version = _header_protocol_version(request)
    raw_header_method = request.headers.get(MCP_METHOD_HEADER)
    raw_header_name = request.headers.get(MCP_NAME_HEADER)
    header_method = raw_header_method.strip() if raw_header_method is not None else None
    body_version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    client_capabilities = meta.get("io.modelcontextprotocol/clientCapabilities") if isinstance(meta, dict) else None
    has_mcp_meta = isinstance(meta, dict) and any(k.startswith("io.modelcontextprotocol/") for k in meta)
    return (
        rpc_request.method == "server/discover"
        or header_version == MCP_PROTOCOL_VERSION
        or body_version is not None
        or client_capabilities is not None
        or has_mcp_meta
        or header_method is not None
        or raw_header_name is not None
        or (header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS)
    )


def _request_metadata_error(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    discovered_tools: "dict[str, Any]",
) -> "Response[Any] | None":
    params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
    meta = params.get("_meta")
    if "_meta" in params and not isinstance(meta, dict):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="Missing or invalid params._meta",
            status_code=HTTP_400_BAD_REQUEST,
        )

    if not _is_modern_2026_rpc(request, rpc_request):
        return None

    header_version = _header_protocol_version(request)
    if header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _unsupported_version_error(rpc_request.id, header_version)
    raw_header_method = request.headers.get(MCP_METHOD_HEADER)
    raw_header_name = request.headers.get(MCP_NAME_HEADER)
    header_method = raw_header_method.strip() if raw_header_method is not None else None
    body_version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    client_capabilities = meta.get("io.modelcontextprotocol/clientCapabilities") if isinstance(meta, dict) else None

    if not isinstance(meta, dict):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="Missing or invalid params._meta",
            status_code=HTTP_400_BAD_REQUEST,
        )
    if not isinstance(body_version, str) or not isinstance(client_capabilities, dict):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="Missing required MCP request metadata",
            status_code=HTTP_400_BAD_REQUEST,
        )
    if header_version != body_version:
        return _error(
            rpc_request.id,
            code=HEADER_MISMATCH,
            message="MCP-Protocol-Version header does not match request metadata",
            status_code=HTTP_400_BAD_REQUEST,
        )
    if header_method != rpc_request.method:
        return _error(
            rpc_request.id,
            code=HEADER_MISMATCH,
            message="Mcp-Method header does not match request method",
            status_code=HTTP_400_BAD_REQUEST,
        )
    if body_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _unsupported_version_error(rpc_request.id, body_version)
    if rpc_request.method in _MODERN_DISALLOWED_METHODS:
        return _error(
            rpc_request.id,
            code=METHOD_NOT_FOUND,
            message=f"Method not found: {rpc_request.method}",
            status_code=HTTP_404_NOT_FOUND,
        )
    if "io.modelcontextprotocol/logLevel" in meta:
        meta_level = meta["io.modelcontextprotocol/logLevel"]
        if not isinstance(meta_level, str) or meta_level not in LOG_LEVEL_SEVERITY:
            return _error(
                rpc_request.id,
                code=INVALID_PARAMS,
                message="Invalid io.modelcontextprotocol/logLevel",
                status_code=HTTP_400_BAD_REQUEST,
            )

    name_field = MCP_NAME_FIELDS.get(rpc_request.method)
    if name_field is not None:
        body_name = params.get(name_field)
        try:
            decoded_name = decode_mcp_header_value(raw_header_name.strip()) if raw_header_name is not None else None
        except ValueError as exc:
            return _error(
                rpc_request.id,
                code=HEADER_MISMATCH,
                message=str(exc),
                status_code=HTTP_400_BAD_REQUEST,
            )
        if not isinstance(body_name, str) or decoded_name != body_name:
            return _error(
                rpc_request.id,
                code=HEADER_MISMATCH,
                message="Mcp-Name header does not match request parameters",
                status_code=HTTP_400_BAD_REQUEST,
            )
    if rpc_request.method != "tools/call":
        return None
    return _param_header_error(request, rpc_request, discovered_tools)


def _param_header_error(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    discovered_tools: "dict[str, Any]",
) -> "Response[Any] | None":
    params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
    tool_name = params.get("name")
    handler = discovered_tools.get(tool_name) if isinstance(tool_name, str) else None
    if handler is None:
        return None
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return None
    for path, custom_name, _property_schema in iter_mcp_header_fields(cached_schema_for_handler(handler)):
        value: Any = arguments
        present = True
        for part in path:
            if not isinstance(value, dict) or part not in value:
                present = False
                break
            value = value[part]
        header_value = request.headers.get(f"Mcp-Param-{custom_name}")
        if not present or value is None:
            if header_value is None:
                continue
        elif header_value is not None:
            try:
                decoded_value = decode_mcp_header_value(header_value.strip())
            except ValueError:
                decoded_value = None
            expected = ("true" if value else "false") if isinstance(value, bool) else str(value)
            if decoded_value == expected:
                continue
        return _error(
            rpc_request.id,
            code=HEADER_MISMATCH,
            message=f"Mcp-Param-{custom_name} header does not match request arguments",
            status_code=HTTP_400_BAD_REQUEST,
        )
    return None


def _request_subject(request: "Request[Any, Any, Any]") -> "str | None":
    auth = request.scope.get("auth")
    if isinstance(auth, dict):
        sub = auth.get("sub")
        if isinstance(sub, str) and sub:
            return sub
    user = request.scope.get("user")
    if isinstance(user, dict):
        for key in ("id", "sub"):
            value = user.get(key)
            if value is not None:
                return str(value)
    else:
        for attribute in ("id", "sub"):
            value = getattr(user, attribute, None)
            if value is not None:
                return str(value)
    return None


def _request_owner_id(request: "Request[Any, Any, Any]") -> "str | None":
    explicit_owner = request.scope.get(MCP_OWNER_ID_SCOPE_KEY)
    if explicit_owner is not None:
        return str(explicit_owner)
    sub = _request_subject(request)
    return f"user:{sub}" if sub is not None else None


async def _handle_client_notification(
    request: "Request[Any, Any, Any]",
    config: "MCPConfig",
    header_version: "str | None",
) -> "Response[Any]":
    if header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _unsupported_version_error(None, header_version)
    session: MCPSession | None = None
    if header_version != MCP_PROTOCOL_VERSION:
        raw_session_id = request.headers.get(MCP_SESSION_ID_HEADER)
        if raw_session_id is not None:
            session = await get_session_store(request.app, config).get(
                raw_session_id.strip(),
                _request_owner_id(request),
            )
            if session is None:
                return _session_not_found_error(None, protocol_version=header_version or MCP_PROTOCOL_VERSION)
    fallback_version = session.protocol_version if session is not None else MCP_PROTOCOL_VERSION
    resolved_version = _supported_header_version(header_version) or fallback_version
    response: Response[Any] = Response(content=b"", status_code=HTTP_202_ACCEPTED, media_type=MediaType.TEXT)
    response.headers[MCP_PROTOCOL_VERSION_HEADER] = resolved_version
    return response


async def _resolve_post_session(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    config: "MCPConfig",
    *,
    header_version: "str | None",
    is_modern_2026: "bool",
) -> "tuple[MCPSession | None, Response[Any] | None]":
    if is_modern_2026 or rpc_request.method == "initialize":
        return None, None
    raw_session_id = request.headers.get(MCP_SESSION_ID_HEADER)
    if raw_session_id is None:
        return None, None
    session = await get_session_store(request.app, config).get(raw_session_id.strip(), _request_owner_id(request))
    if session is None:
        err_version = _supported_header_version(header_version) or MCP_PROTOCOL_VERSION
        return None, _session_not_found_error(rpc_request.id, protocol_version=err_version)
    return session, None


def _sync_session_logging(registry: "Registry", config: "MCPConfig", session: "MCPSession | None") -> "None":
    if not config.logging or not registry.has_subscription_manager:
        return
    manager = registry.subscription_manager
    set_default = getattr(manager, "set_default_log_level", None)
    if callable(set_default):
        set_default(config.default_log_level)
    if session is not None and session.log_level is not None and session.log_level in LOG_LEVEL_SEVERITY:
        set_session = getattr(manager, "set_session_log_level", None)
        if callable(set_session):
            set_session(session.session_id, session.log_level)


async def _close_registry_session_streams(registry: "Registry", session_id: "str") -> "None":
    if not registry.has_subscription_manager:
        return
    close_session = getattr(registry.subscription_manager, "close_session", None)
    if callable(close_session):
        outcome = close_session(session_id)
        if inspect.isawaitable(outcome):
            await outcome


def _make_subscription_authorizer(
    request: "Request[Any, Any, Any]",
    config: "MCPConfig",
) -> "Callable[[str, dict[str, Any]], Awaitable[bool]] | None":
    authorizer = config.subscription_authorizer
    if authorizer is None:
        return None

    async def _authorize(method: "str", params: "dict[str, Any]") -> "bool":
        if method != "notifications/resources/updated":
            return True
        uri = params.get("uri")
        if not isinstance(uri, str):
            return True
        return await evaluate_subscription_authorizer(authorizer, request, uri)

    return _authorize


def _progress_token(rpc_request: "JSONRPCRequest") -> "str | int | None":
    params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
    meta = params.get("_meta")
    token = meta.get("progressToken") if isinstance(meta, dict) else None
    return token if isinstance(token, (str, int)) and not isinstance(token, bool) else None


def _resolve_request_logging(
    *,
    meta: "dict[str, Any]",
    owner_id: "str | None",
    config: "MCPConfig",
    registry: "Registry",
    session: "MCPSession | None",
    log_reporter: "Callable[[str, Any, str | None], Awaitable[None]] | None",
) -> "tuple[str | None, Callable[[str, Any, str | None], Awaitable[None]] | None]":
    if not config.logging:
        return None, None
    _sync_session_logging(registry, config, session)
    raw_meta_level = meta.get("io.modelcontextprotocol/logLevel")
    meta_level = raw_meta_level if isinstance(raw_meta_level, str) and raw_meta_level in LOG_LEVEL_SEVERITY else None
    resolved_level: str | None
    if meta_level is not None:
        resolved_level = meta_level
    elif session is not None:
        if session.log_level is not None and session.log_level in LOG_LEVEL_SEVERITY:
            resolved_level = session.log_level
        elif registry.has_subscription_manager:
            get_level = getattr(registry.subscription_manager, "get_session_log_level", None)
            resolved_level = (
                str(get_level(session.session_id, default=config.default_log_level))
                if callable(get_level)
                else config.default_log_level
            )
        else:
            resolved_level = config.default_log_level
    else:
        resolved_level = None

    if log_reporter is not None:
        return resolved_level, log_reporter

    target_session_id = session.session_id if session is not None else None

    async def _default_log_reporter(level: "str", data: "Any", logger_name: "str | None") -> "None":
        if not registry.has_subscription_manager:
            return
        payload: dict[str, Any] = {"level": level, "data": data}
        if logger_name is not None:
            payload["logger"] = logger_name
        await registry.subscription_manager.publish(
            "notifications/message",
            payload,
            owner_id=owner_id,
            session_id=target_session_id,
        )

    return resolved_level, _default_log_reporter


def _build_request_context(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    progress_reporter: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
    *,
    config: "MCPConfig",
    registry: "Registry",
    session: "MCPSession | None" = None,
    log_reporter: "Callable[[str, Any, str | None], Awaitable[None]] | None" = None,
) -> "MCPRequestContext":
    params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
    raw_meta = params.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    raw_client_info = meta.get("io.modelcontextprotocol/clientInfo")
    client_info: dict[str, Any] | None
    if isinstance(raw_client_info, dict):
        client_info = raw_client_info
    else:
        top_client_info = params.get("clientInfo")
        if isinstance(top_client_info, dict):
            client_info = top_client_info
        elif session is not None and session.client_info is not None:
            client_info = dict(session.client_info)
        else:
            client_info = None
    client_id = client_info.get("name") if isinstance(client_info, dict) else None
    raw_caps = meta.get("io.modelcontextprotocol/clientCapabilities")
    client_capabilities: dict[str, Any] | None
    if isinstance(raw_caps, dict):
        client_capabilities = raw_caps
    else:
        top_caps = params.get("capabilities")
        if isinstance(top_caps, dict):
            client_capabilities = top_caps
        elif session is not None and session.client_capabilities is not None:
            client_capabilities = dict(session.client_capabilities)
        else:
            client_capabilities = None
    header_version = _header_protocol_version(request)
    body_version = meta.get("io.modelcontextprotocol/protocolVersion")
    if isinstance(body_version, str) and body_version:
        resolved_version = body_version
    elif header_version:
        resolved_version = header_version
    elif session is not None:
        resolved_version = session.protocol_version
    else:
        resolved_version = MCP_PROTOCOL_VERSION
    owner_id = _request_owner_id(request)
    progress_token = _progress_token(rpc_request) if progress_reporter is not None else None
    resolved_log_level, resolved_log_reporter = _resolve_request_logging(
        meta=meta,
        owner_id=owner_id,
        config=config,
        registry=registry,
        session=session,
        log_reporter=log_reporter,
    )

    return MCPRequestContext(
        client_id=client_id or "anonymous",
        owner_id=owner_id,
        request=request,
        protocol_version=resolved_version,
        client_capabilities=client_capabilities,
        client_info=client_info,
        input_responses=params.get("inputResponses"),
        request_state=params.get("requestState"),
        progress_token=progress_token,
        progress_reporter=progress_reporter,
        session_id=session.session_id if session is not None else None,
        log_level=resolved_log_level,
        log_reporter=resolved_log_reporter,
    )


def _ensure_router(
    app: "Litestar",
    config: "MCPConfig",
    discovered_tools: "dict[str, Any]",
    discovered_resources: "dict[str, Any]",
    discovered_prompts: "dict[str, PromptRegistration]",
    registry: "Registry",
    task_store: "MCPTaskStore | None",
    skill_catalog: "SkillCatalog | None",
) -> "JSONRPCRouter":
    if not hasattr(app.state, "mcp_router"):
        app.state.mcp_router = _build_cached_router(
            app,
            config,
            discovered_tools,
            discovered_resources,
            discovered_prompts,
            registry,
            task_store,
            skill_catalog,
        )
    router: JSONRPCRouter = app.state.mcp_router
    return router


def _build_cached_router(
    app: "Litestar",
    config: "MCPConfig",
    discovered_tools: "dict[str, Any]",
    discovered_resources: "dict[str, Any]",
    discovered_prompts: "dict[str, PromptRegistration]",
    registry: "Registry",
    task_store: "MCPTaskStore | None",
    skill_catalog: "SkillCatalog | None",
) -> "JSONRPCRouter":
    router = JSONRPCRouter()

    def service() -> "MCPHandlerService":
        return MCPHandlerService(
            config=config,
            discovered_tools=discovered_tools,
            discovered_resources=discovered_resources,
            discovered_prompts=discovered_prompts,
            app_ref=app,
            registry=registry,
            task_store=task_store,
            skill_catalog=skill_catalog,
        )

    MCPHandlerService.register_routes(
        router,
        service,
        task_enabled=task_store is not None and config.task_config is not None,
        skill_catalog=skill_catalog,
        logging_enabled=config.logging,
    )
    return router


def _server_info(app: "Litestar", config: "MCPConfig") -> "dict[str, str]":
    openapi = app.openapi_config
    return {
        "name": config.name or (openapi.title if openapi is not None else "Litestar MCP Server"),
        "version": openapi.version if openapi is not None else "1.0.0",
    }


def _finalize_result(
    payload: "dict[str, Any]",
    *,
    method: "str",
    app: "Litestar",
    config: "MCPConfig",
) -> "None":
    result = payload.get("result")
    if not isinstance(result, dict):
        return
    result.setdefault("resultType", "complete")
    meta = result.setdefault("_meta", {})
    meta.setdefault("io.modelcontextprotocol/serverInfo", _server_info(app, config))
    if method in _CACHEABLE_METHODS:
        result.setdefault("ttlMs", config.cache_ttl_ms)
        result.setdefault("cacheScope", config.cache_scope)


def _notification_unsupported(request_id: "Any") -> "dict[str, Any]":
    return error_response(
        request_id,
        JSONRPCError(code=INVALID_PARAMS, message="Client notifications are not supported over Streamable HTTP"),
    )


async def _dispatch_jsonrpc_response(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    router: "JSONRPCRouter",
    config: "MCPConfig",
    registry: "Registry",
    *,
    is_modern_2026: "bool",
    session: "MCPSession | None",
) -> "Response[Any]":
    app = request.app
    context = _build_request_context(request, rpc_request, config=config, registry=registry, session=session)
    result = await router.dispatch(rpc_request, context)
    if result is None:
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="Client notifications are not supported over Streamable HTTP",
            status_code=HTTP_400_BAD_REQUEST,
            protocol_version=MCP_PROTOCOL_VERSION if is_modern_2026 else context.protocol_version,
        )
    _finalize_result(result, method=rpc_request.method, app=app, config=config)

    created_session_id: str | None = None
    response_version = MCP_PROTOCOL_VERSION if is_modern_2026 else context.protocol_version
    result_payload = result.get("result")
    if rpc_request.method == "initialize" and "error" not in result and isinstance(result_payload, dict):
        negotiated = result_payload.get("protocolVersion")
        if isinstance(negotiated, str):
            response_version = negotiated
            if not is_modern_2026 and negotiated != MCP_PROTOCOL_VERSION:
                created = await get_session_store(app, config).create(
                    protocol_version=negotiated,
                    owner_id=context.owner_id,
                    client_capabilities=context.client_capabilities,
                    client_info=context.client_info,
                )
                created_session_id = created.session_id

    error = result.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None
    status_code = (
        HTTP_400_BAD_REQUEST
        if error_code
        in {
            INVALID_PARAMS,
            HEADER_MISMATCH,
            MISSING_REQUIRED_CLIENT_CAPABILITY,
            UNSUPPORTED_PROTOCOL_VERSION,
        }
        else HTTP_200_OK
    )
    response = Response(content=result, status_code=status_code, media_type=MediaType.JSON)
    response.headers[MCP_PROTOCOL_VERSION_HEADER] = response_version
    if created_session_id is not None:
        response.headers[MCP_SESSION_ID_HEADER] = created_session_id
    return response


def _make_progress_log_reporter(
    sender: "MemoryObjectSendStream[dict[str, Any]]",
    *,
    config: "MCPConfig",
    registry: "Registry",
    session: "MCPSession | None",
    owner_id: "str | None",
    meta_level: "str | None",
) -> "Callable[[str, Any, str | None], Awaitable[None]] | None":
    if not config.logging:
        return None

    async def _report_log(level: "str", data: "Any", logger_name: "str | None") -> "None":
        payload: dict[str, Any] = {"level": level, "data": data}
        if logger_name is not None:
            payload["logger"] = logger_name
        if session is not None:
            if registry.has_subscription_manager:
                await registry.subscription_manager.publish(
                    "notifications/message",
                    payload,
                    owner_id=owner_id,
                    session_id=session.session_id,
                )
            return
        if registry.has_subscription_manager:
            await registry.subscription_manager.publish(
                "notifications/message",
                payload,
                owner_id=owner_id,
                session_id=None,
            )
        if log_level_allows(level, meta_level or config.default_log_level):
            try:
                await sender.send({"jsonrpc": "2.0", "method": "notifications/message", "params": payload})
            except (anyio.BrokenResourceError, anyio.ClosedResourceError) as exc:
                raise asyncio.CancelledError from exc

    return _report_log


def _progress_response(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    router: "JSONRPCRouter",
    config: "MCPConfig",
    registry: "Registry",
    session: "MCPSession | None" = None,
) -> "Response[Any]":
    """Answer a request carrying a progress token on its own SSE stream.

    Progress notifications are delivered on the response stream of the request
    that supplied the token, followed by that request's JSON-RPC response.
    """
    app = request.app
    preview_context = _build_request_context(request, rpc_request, config=config, registry=registry, session=session)
    params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
    raw_meta = params.get("_meta")
    raw_meta_level = raw_meta.get("io.modelcontextprotocol/logLevel") if isinstance(raw_meta, dict) else None
    meta_level = raw_meta_level if isinstance(raw_meta_level, str) and raw_meta_level in LOG_LEVEL_SEVERITY else None

    async def run(sender: "MemoryObjectSendStream[dict[str, Any]]") -> "dict[str, Any] | None":
        async def report(progress_params: "dict[str, Any]") -> None:
            try:
                await sender.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": progress_params})
            except (anyio.BrokenResourceError, anyio.ClosedResourceError) as exc:
                raise asyncio.CancelledError from exc

        progress_log_reporter = _make_progress_log_reporter(
            sender,
            config=config,
            registry=registry,
            session=session,
            owner_id=preview_context.owner_id,
            meta_level=meta_level,
        )
        return await router.dispatch(
            rpc_request,
            _build_request_context(
                request,
                rpc_request,
                report,
                config=config,
                registry=registry,
                session=session,
                log_reporter=progress_log_reporter,
            ),
        )

    owner = start_stream(
        request.scope,
        run,
        capacity=config.stream_queue_capacity,
        cleanup_timeout=config.stream_cleanup_timeout,
    )

    async def event_stream() -> "AsyncGenerator[ServerSentEventMessage, None]":
        try:
            async for message in owner.receiver:
                yield ServerSentEventMessage(data=to_json(message))
            result = await owner.result()
            if result is None:
                result = _notification_unsupported(rpc_request.id)
            _finalize_result(result, method=rpc_request.method, app=app, config=config)
            yield ServerSentEventMessage(data=to_json(result))
        finally:
            await owner.close()

    response = ServerSentEvent(event_stream())
    response.headers[MCP_PROTOCOL_VERSION_HEADER] = preview_context.protocol_version
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _stream_subscription_events(
    stream_id: "str",
    stream: "AsyncGenerator[dict[str, Any], None]",
    registry: "Registry",
    config: "MCPConfig",
    *,
    protocol_version: "str" = MCP_PROTOCOL_VERSION,
) -> "Response[Any]":
    async def event_stream() -> "AsyncGenerator[ServerSentEventMessage, None]":
        next_message = asyncio.create_task(stream.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {next_message},
                    timeout=config.subscription_keepalive_seconds,
                )
                if not done:
                    yield ServerSentEventMessage(comment="keepalive")
                    continue
                try:
                    message = next_message.result()
                except StopAsyncIteration:
                    return
                yield ServerSentEventMessage(data=to_json(message))
                next_message = asyncio.create_task(stream.__anext__())
        finally:
            next_message.cancel()
            if next_message.done() and not next_message.cancelled():
                with contextlib.suppress(BaseException):
                    next_message.exception()
            with anyio.CancelScope(shield=True):
                await registry.subscription_manager.disconnect(stream_id)

    response = ServerSentEvent(event_stream())
    response.headers[MCP_PROTOCOL_VERSION_HEADER] = protocol_version
    response.headers["X-Accel-Buffering"] = "no"
    return response


async def _subscription_response(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    registry: "Registry",
    config: "MCPConfig",
) -> "Response[Any]":
    notifications = rpc_request.params.get("notifications")
    if not isinstance(notifications, dict):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="subscriptions/listen notifications must be an object",
            status_code=HTTP_400_BAD_REQUEST,
        )
    raw_resources = notifications.get("resourceSubscriptions")
    if raw_resources is not None and (
        not isinstance(raw_resources, list) or any(not isinstance(uri, str) or not uri for uri in raw_resources)
    ):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="subscriptions/listen resourceSubscriptions must be a list of non-empty resource URI strings",
            status_code=HTTP_400_BAD_REQUEST,
        )
    if config.subscription_authorizer is not None and isinstance(raw_resources, list):
        for uri in raw_resources:
            if not await evaluate_subscription_authorizer(config.subscription_authorizer, request, uri):
                return _error(
                    rpc_request.id,
                    code=INVALID_PARAMS,
                    message=f"Not authorized to subscribe to resource: {uri}",
                    status_code=HTTP_400_BAD_REQUEST,
                )

    _sync_session_logging(registry, config, None)
    open_fn = registry.subscription_manager.open
    open_kwargs: dict[str, Any] = {}
    try:
        sig = inspect.signature(open_fn)
    except (TypeError, ValueError):
        sig = None
    if sig is not None:
        accepts_var_kw = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values())
        if accepts_var_kw or "owner_id" in sig.parameters:
            open_kwargs["owner_id"] = _request_owner_id(request)
        if accepts_var_kw or "authorize" in sig.parameters:
            open_kwargs["authorize"] = _make_subscription_authorizer(request, config)

    try:
        stream_id, stream = await open_fn(rpc_request.id, notifications, **open_kwargs)
    except (StreamLimitExceeded, ValueError) as exc:
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message=str(exc),
            status_code=HTTP_400_BAD_REQUEST,
        )
    return _stream_subscription_events(stream_id, stream, registry, config, protocol_version=MCP_PROTOCOL_VERSION)


__all__ = (
    "HEADER_MISMATCH",
    "MCP_METHOD_HEADER",
    "MCP_NAME_HEADER",
    "MCP_OWNER_ID_SCOPE_KEY",
    "MCP_PROTOCOL_VERSION",
    "MCP_PROTOCOL_VERSION_HEADER",
    "MCP_SESSION_ID_HEADER",
    "MISSING_REQUIRED_CLIENT_CAPABILITY",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "MCPController",
    "decode_mcp_header_value",
    "encode_mcp_header_value",
)
