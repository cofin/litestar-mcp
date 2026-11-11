# ruff: noqa: PLR0915, C901
"""MCP service layer handler."""

import asyncio
import base64
import contextlib
import contextvars
import copy
import inspect
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeVar, cast, get_type_hints, overload
from uuid import uuid4

import anyio
import msgspec
from litestar import Request
from litestar.exceptions import SerializationException
from litestar.stores.base import Store  # noqa: TC002
from litestar.stores.memory import MemoryStore

from litestar_mcp.core._cursor import decode_cursor, encode_cursor
from litestar_mcp.core.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JSONRPCError,
    JSONRPCErrorException,
)
from litestar_mcp.core.schema_builder import (
    cached_output_schema_for_handler,
    cached_schema_for_handler,
    unwrap_root_object_schema,
)
from litestar_mcp.core.serialization import decode_json, encode_json
from litestar_mcp.core.sse import LOG_LEVEL_SEVERITY, log_level_allows, validate_log_level
from litestar_mcp.mcp.config import SUPPORTED_PROTOCOL_VERSIONS, UI_EXTENSION, UI_MIME_TYPE
from litestar_mcp.mcp.content import (
    MCPBlobResource,
    MCPInputRequiredResult,
    MCPResourceLink,
    MCPToolResult,
    enforce_blob_size,
    is_content_block,
    normalize_content_blocks,
)
from litestar_mcp.mcp.error_mapping import (
    mcp_error_for_prompt_execution,
    mcp_error_for_resource_not_found,
    mcp_error_for_resource_read,
    mcp_error_for_skill_file_unreadable,
    mcp_error_for_skill_not_found,
)
from litestar_mcp.mcp.executor import (
    MCPHandlerResponse,
    MCPToolErrorResult,
    blank_http_scope,
    execute_handler,
    execute_handler_response,
    execute_tool,
)
from litestar_mcp.mcp.registry import (
    PromptRegistration,
    Registry,
    _clean_icons,
    _clean_meta,
    _clean_title,
    _normalize_prompt_result,
    apply_prompt_instructions,
    render_prompt_entry,
    resolve_prompt_description,
    should_include_prompt,
)
from litestar_mcp.mcp.skills import SKILL_URI_PREFIX, SkillIntegrityError, filter_skills, parse_skill_uri
from litestar_mcp.mcp.tasks import (
    MCPTaskStore,
    PrincipalProxy,
    TaskLookupError,
    TaskRecord,
    ToolTaskCall,
    extract_principal_snapshot,
    rehydrate_security_scope,
)
from litestar_mcp.utils import (
    classify_resource_uri,
    get_handler_function,
    get_mcp_metadata,
    match_uri,
    normalize_task_support,
    render_description,
    should_include_handler,
)
from litestar_mcp.utils.handler_signature import (
    _unwrap_annotated,
    get_advertised_handler_parameters,
    resolve_tool_argument_aliases,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from litestar import Litestar
    from litestar.handlers import BaseRouteHandler

    from litestar_mcp.core.jsonrpc import JSONRPCRouter
    from litestar_mcp.mcp.config import MCPConfig
    from litestar_mcp.mcp.registry import ResourceTemplate
    from litestar_mcp.mcp.skills import Skill, SkillCatalog
    from litestar_mcp.utils import Kind

_logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2026-07-28"
SESSION_STORE_NAME: str = "litestar_mcp_sessions"
TASKS_EXTENSION = "io.modelcontextprotocol/tasks"

SKILLS_EXTENSION = "io.modelcontextprotocol/skills"
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
_MAX_SESSION_ID_LENGTH = 512
_MIN_VISIBLE_ASCII = 0x21
_MAX_VISIBLE_ASCII = 0x7E


class MCPSession(msgspec.Struct, kw_only=True):
    """Persisted state for a pre-`2026-07-28` Streamable HTTP MCP session."""

    session_id: str
    """Opaque visible-ASCII identifier carried in the `Mcp-Session-Id` HTTP header."""
    protocol_version: str
    """Negotiated MCP protocol version string bound to this session."""
    owner_id: str | None = None
    """Optional authenticated owner identifier isolating session access across callers."""
    client_capabilities: dict[str, Any] | None = None
    """Client capabilities dictionary captured during the `initialize` handshake."""
    client_info: dict[str, Any] | None = None
    """Client implementation metadata (`name`, `version`) captured during `initialize`."""
    subscriptions: list[str] = msgspec.field(default_factory=list)
    """Deduplicated resource URIs currently subscribed to by this session."""
    log_level: str | None = None
    """Optional minimum RFC 5424 log severity set via `logging/setLevel`."""


class MCPSessionStore:
    """Litestar `Store`-backed persistence for `Mcp-Session-Id` session records."""

    def __init__(self, store: "Store", *, ttl_seconds: "float" = 3600.0) -> "None":
        if isinstance(ttl_seconds, bool) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            msg = "ttl_seconds must be positive and finite"
            raise ValueError(msg)
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    @property
    def ttl_seconds(self) -> "float":
        """Return the configured session TTL in seconds."""
        return self._ttl_seconds

    async def create(
        self,
        *,
        protocol_version: "str",
        owner_id: "str | None",
        client_capabilities: "dict[str, Any] | None" = None,
        client_info: "dict[str, Any] | None" = None,
    ) -> "MCPSession":
        """Create and persist a new owner-scoped MCP session record."""
        session = MCPSession(
            session_id=uuid4().hex,
            protocol_version=protocol_version,
            owner_id=owner_id,
            client_capabilities=copy.deepcopy(client_capabilities) if client_capabilities is not None else None,
            client_info=copy.deepcopy(client_info) if client_info is not None else None,
        )
        await self.save(session)
        return session

    async def get(self, session_id: "str", owner_id: "str | None") -> "MCPSession | None":
        """Retrieve an active session matching `session_id` and `owner_id`, or `None`."""
        session = await self._load(session_id)
        if session is None or session.owner_id != owner_id:
            return None
        return session

    async def save(self, session: "MCPSession") -> "None":
        """Persist `session` under its configured TTL window."""
        await self._store.set(
            self._key(session.session_id),
            encode_json(session),
            expires_in=self._expires_in(),
        )

    async def delete(self, session_id: "str", owner_id: "str | None") -> "bool":
        """Delete a session when it exists and belongs to `owner_id`."""
        async with self._lock:
            session = await self._load(session_id)
            if session is None or session.owner_id != owner_id:
                return False
            await self._store.delete(self._key(session_id))
            return True

    async def add_subscription(
        self,
        session_id: "str",
        owner_id: "str | None",
        uri: "str",
    ) -> "MCPSession | None":
        """Append `uri` to the session's active subscriptions under lock."""

        def _apply(session: "MCPSession") -> "None":
            if uri not in session.subscriptions:
                session.subscriptions.append(uri)

        return await self._mutate(session_id, owner_id, _apply)

    async def remove_subscription(
        self,
        session_id: "str",
        owner_id: "str | None",
        uri: "str",
    ) -> "MCPSession | None":
        """Remove `uri` from the session's active subscriptions under lock."""

        def _apply(session: "MCPSession") -> "None":
            if uri in session.subscriptions:
                session.subscriptions = [item for item in session.subscriptions if item != uri]

        return await self._mutate(session_id, owner_id, _apply)

    async def set_log_level(
        self,
        session_id: "str",
        owner_id: "str | None",
        level: "str",
    ) -> "MCPSession | None":
        """Persist the active minimum log severity on the session under lock."""

        def _apply(session: "MCPSession") -> "None":
            session.log_level = level

        return await self._mutate(session_id, owner_id, _apply)

    def _expires_in(self) -> "int":
        return max(1, math.ceil(self._ttl_seconds))

    @staticmethod
    def _key(session_id: "str") -> "str":
        return f"mcp-session:{session_id}"

    @staticmethod
    def _is_valid_session_id(session_id: "str") -> "bool":
        return (
            isinstance(session_id, str)
            and bool(session_id)
            and len(session_id) <= _MAX_SESSION_ID_LENGTH
            and all(_MIN_VISIBLE_ASCII <= ord(ch) <= _MAX_VISIBLE_ASCII for ch in session_id)
        )

    async def _load(self, session_id: "str") -> "MCPSession | None":
        if not self._is_valid_session_id(session_id):
            return None
        raw = await self._store.get(self._key(session_id))
        if raw is None:
            return None
        try:
            return decode_json(raw, target_type=MCPSession)
        except (SerializationException, msgspec.MsgspecError, ValueError, TypeError):
            return None

    async def _mutate(
        self,
        session_id: "str",
        owner_id: "str | None",
        mutator: "Callable[[MCPSession], None]",
    ) -> "MCPSession | None":
        async with self._lock:
            session = await self._load(session_id)
            if session is None or session.owner_id != owner_id:
                return None
            mutator(session)
            await self.save(session)
            return session


def resolve_session_store(app: "Litestar") -> "Store":
    """Resolve the session `Store` from `app.stores` with a `MemoryStore` fallback on `app.state`."""
    try:
        return app.stores.get(SESSION_STORE_NAME)
    except (KeyError, TypeError):
        existing: Any = app.state.get("_mcp_session_store")
        if existing is None:
            existing = MemoryStore()
            app.state["_mcp_session_store"] = existing
        return cast("Store", existing)


def get_session_store(app: "Litestar", config: "MCPConfig") -> "MCPSessionStore":
    """Return the cached `MCPSessionStore` bound to `app.state`."""
    existing: Any = getattr(app.state, "mcp_session_store", None)
    if isinstance(existing, MCPSessionStore) and existing.ttl_seconds == config.session_ttl_seconds:
        return existing
    session_store = MCPSessionStore(resolve_session_store(app), ttl_seconds=config.session_ttl_seconds)
    app.state.mcp_session_store = session_store
    return session_store


@dataclass
class MCPRequestContext:
    """Request context threaded through tool and task execution.

    Authentication lives in Litestar middleware; ``request.user`` and
    ``request.auth`` are the per-request source of truth for tool handlers.
    This struct only carries the scope identifiers used by MCP itself
    (client id, task-owner id, and the live request handle).
    """

    client_id: "str"
    """Identifier of the connected MCP client or transport session."""
    owner_id: "str | None"
    """Authenticated task-owner identifier, or `None` for anonymous callers."""
    request: "Request[Any, Any, Any] | None" = None
    """Inbound Litestar HTTP `Request`, or `None` for stdio/background invocations."""
    protocol_version: "str" = MCP_PROTOCOL_VERSION
    """Negotiated MCP protocol version string for the current request."""
    client_capabilities: "dict[str, Any] | None" = None
    """Client capabilities dictionary advertised during initialization or via request `_meta`."""
    client_info: "dict[str, Any] | None" = None
    """Client implementation metadata (`name`, `version`) supplied by the caller."""
    input_responses: "dict[str, Any] | None" = None
    """Multi-round-trip input responses supplied by the client for an interactive tool call."""
    request_state: "str | None" = None
    """Opaque round-tripped state token accompanying `input_responses`."""
    progress_token: "str | int | None" = None
    """Optional progress correlation token extracted from request `_meta.progressToken`."""
    progress_reporter: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None
    """Optional async callback used to emit `notifications/progress` frames for this request."""
    ui_capabilities: "dict[str, Any] | None" = None
    """Negotiated SEP-1865 MCP Apps UI client capabilities extracted from `client_capabilities`."""
    session_id: "str | None" = None
    """Active `Mcp-Session-Id` identifier associated with the request, if any."""
    log_level: "str | None" = None
    """Optional minimum RFC 5424 log severity threshold enforced by `log()` for this execution."""
    log_reporter: "Callable[[str, Any, str | None], Awaitable[None]] | None" = None
    """Optional async callback used to emit `notifications/message` entries for this execution."""

    def __post_init__(self) -> "None":
        if self.ui_capabilities is None and isinstance(self.client_capabilities, Mapping):
            extensions: Any = self.client_capabilities.get("extensions")
            if isinstance(extensions, Mapping):
                ui_caps: Any = extensions.get(UI_EXTENSION)
                if isinstance(ui_caps, Mapping):
                    self.ui_capabilities = copy.deepcopy(dict(ui_caps))

    async def report_progress(self, progress: float, total: float | None = None, message: str | None = None) -> None:
        """Report progress correlated to the request's supplied progress token."""
        if self.progress_token is None or self.progress_reporter is None:
            msg = "This request did not supply a progress token"
            raise RuntimeError(msg)
        params: dict[str, Any] = {"progressToken": self.progress_token, "progress": progress}
        if total is not None:
            params["total"] = total
        if message is not None:
            params["message"] = message
        await self.progress_reporter(params)

    async def log(self, level: "str", data: "Any", logger: "str | None" = None) -> "None":
        """Emit a structured RFC 5424 MCP log notification gated by the active severity threshold."""
        validate_log_level(level)
        if self.log_level is not None and not log_level_allows(level, self.log_level):
            return
        if self.log_reporter is None:
            return
        await self.log_reporter(level, data, logger)


_request_context: contextvars.ContextVar[MCPRequestContext | None] = contextvars.ContextVar(
    "litestar_mcp_request_context", default=None
)


_MISSING = object()
_T = TypeVar("_T")


@overload
def get_mcp_request_context() -> "MCPRequestContext": ...


@overload
def get_mcp_request_context(default: "_T") -> "MCPRequestContext | _T": ...


def get_mcp_request_context(default: "Any" = _MISSING) -> "Any":
    """Return the context for the currently executing MCP handler, or ``default`` if supplied."""
    context = _request_context.get()
    if context is not None:
        return context
    if default is not _MISSING:
        return default
    msg = "No MCP request is currently executing"
    raise RuntimeError(msg)


def _paginate_list(items: "list[_T]", params: "dict[str, Any]", page_size: "int") -> "tuple[list[_T], str | None]":
    """Slice ``items`` by the opaque cursor in ``params`` and return ``(page, next_cursor)``."""
    cursor = params.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise JSONRPCErrorException(
            JSONRPCError(code=INVALID_PARAMS, message="The 'cursor' parameter must be a string")
        )
    try:
        offset = decode_cursor(cursor) if cursor else 0
    except ValueError as exc:
        raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
    page = items[offset : offset + page_size]
    next_cursor = encode_cursor(offset + page_size) if offset + page_size < len(items) else None
    return page, next_cursor


def _serialize_tool_content(value: "Any") -> "str":
    if isinstance(value, str):
        return value
    return encode_json(value).decode("utf-8")


def _is_resource_text_media_type(mime_type: "str") -> "bool":
    """Return whether a resource body should be sent as MCP text."""
    base = mime_type.split(";", 1)[0].strip().lower()
    return (
        base.startswith("text/")
        or base == "application/json"
        or base.endswith(("+json", "+xml"))
        or base
        in {
            "application/javascript",
            "application/xml",
            "application/x-yaml",
            "application/yaml",
        }
    )


def _explicit_resource_mime_type(handler: "BaseRouteHandler", config: "MCPConfig") -> "str | None":
    """Return explicitly configured MCP resource MIME metadata from handler opt or decorator metadata."""
    opt = getattr(handler, "opt", None) or {}
    opt_mime_type = opt.get(config.opt_keys.resource_mime_type)
    if isinstance(opt_mime_type, str) and opt_mime_type:
        return opt_mime_type
    fn = get_handler_function(handler)
    metadata = get_mcp_metadata(handler, kind="resource") or get_mcp_metadata(fn, kind="resource") or {}
    metadata_mime_type = metadata.get("mime_type")
    if isinstance(metadata_mime_type, str) and metadata_mime_type:
        return metadata_mime_type
    return None


def _handler_media_type(handler: "BaseRouteHandler") -> "str | None":
    """Return the normalized string value of a route handler's declared or inferred `media_type`."""
    media_type = getattr(handler, "media_type", None)
    handler_media_type = getattr(media_type, "value", media_type)
    if isinstance(handler_media_type, str) and handler_media_type:
        return handler_media_type
    return None


def _handler_tags(handler: "BaseRouteHandler") -> "set[str]":
    """Resolve effective tags for a Litestar route handler including router and controller ancestry."""
    resolve_tags = getattr(handler, "resolve_tags", None)
    if callable(resolve_tags):
        return set(cast("Iterable[str]", resolve_tags()))
    return set(cast("Iterable[str]", getattr(handler, "tags", None) or ()))


def _apply_descriptor_fields(
    entry: "dict[str, Any]",
    *,
    title: "str | None",
    icons: "list[dict[str, Any]] | None",
    meta: "dict[str, Any] | None",
) -> "None":
    """Attach non-empty `title`, `icons`, and `_meta` fields onto an MCP wire descriptor."""
    if title is not None:
        entry["title"] = title
    if icons is not None:
        entry["icons"] = icons
    if meta is not None:
        entry["_meta"] = meta


def _resource_mime_type(handler: "BaseRouteHandler", config: "MCPConfig") -> "str":
    """Resolve resource MIME metadata.

    Precedence: the `resource_mime_type` opt key, decorator `mime_type`
    metadata, then the route handler's own `media_type` (so
    `@get(..., media_type="application/pdf")` needs no MCP-specific override).
    """
    explicit = _explicit_resource_mime_type(handler, config)
    if explicit is not None:
        return explicit
    handler_media_type = _handler_media_type(handler)
    if handler_media_type:
        return handler_media_type
    return "application/json"


def _resource_uri(name: "str", handler: "BaseRouteHandler", config: "MCPConfig") -> "str":
    """Resolve a resource's concrete URI override."""
    opt = getattr(handler, "opt", None) or {}
    configured_uri = opt.get(config.opt_keys.resource_uri)
    if isinstance(configured_uri, str) and configured_uri:
        return configured_uri
    fn = get_handler_function(handler)
    metadata = get_mcp_metadata(handler, kind="resource") or get_mcp_metadata(fn, kind="resource") or {}
    metadata_uri = metadata.get("resource_uri")
    if isinstance(metadata_uri, str) and metadata_uri:
        return metadata_uri
    opt_template = opt.get(config.opt_keys.resource_template)
    if isinstance(opt_template, str) and opt_template:
        with contextlib.suppress(ValueError):
            static_uri, _ = classify_resource_uri(uri_template=opt_template)
            if static_uri is not None:
                return static_uri
    return f"litestar://{name}"


def _without_undefined_values(value: "Any") -> "Any":
    """Remove Litestar OpenAPI undefined sentinels before JSON encoding."""
    if isinstance(value, dict):
        return {key: _without_undefined_values(item) for key, item in value.items() if type(item) is not object}
    if isinstance(value, list):
        return [_without_undefined_values(item) for item in value if type(item) is not object]
    return value


def _resource_content_from_bytes(
    uri: "str", body: "bytes", *, mime_type: "str", max_blob_bytes: "int | None"
) -> "dict[str, Any]":
    """Build one MCP ResourceContents object from raw bytes."""
    content: dict[str, Any] = {"uri": uri, "mimeType": mime_type}
    if _is_resource_text_media_type(mime_type):
        try:
            content["text"] = body.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return content
    enforce_blob_size(len(body), max_blob_bytes=max_blob_bytes)
    content["blob"] = base64.b64encode(body).decode("ascii")
    return content


def _resource_content_from_response(
    uri: "str",
    response: "MCPHandlerResponse",
    *,
    fallback_mime_type: "str",
    explicit_mime_type: "str | None" = None,
    handler_media_type: "str | None" = None,
    max_blob_bytes: "int | None",
) -> "dict[str, Any]":
    """Build one MCP ResourceContents object from a captured handler response."""
    resolved_mime_type = response.media_type or fallback_mime_type
    if (
        explicit_mime_type
        and not response.explicit_response_media_type
        and (
            not response.media_type
            or (response.media_type == "text/plain" and handler_media_type in {None, "text/plain"})
            or (response.media_type == "application/json" and _is_resource_text_media_type(explicit_mime_type))
        )
    ):
        resolved_mime_type = explicit_mime_type
    if (
        ";" in fallback_mime_type
        and ";" not in resolved_mime_type
        and resolved_mime_type.lower() == fallback_mime_type.split(";", 1)[0].strip().lower()
    ):
        resolved_mime_type = fallback_mime_type
    return _resource_content_from_bytes(uri, response.body, mime_type=resolved_mime_type, max_blob_bytes=max_blob_bytes)


def _looks_like_tool_content(value: "Any") -> "bool":
    """Return whether a plain handler result is an explicit MCP content block."""
    if isinstance(value, (MCPBlobResource, MCPResourceLink)):
        return True
    if is_content_block(value):
        return True
    if isinstance(value, list) and value:
        return all(isinstance(item, (MCPBlobResource, MCPResourceLink)) or is_content_block(item) for item in value)
    return False


def _non_object_output_error(value: "Any") -> "dict[str, Any]":
    """Build an `isError: True` MCP tool result for a non-object value returned against an object `outputSchema`."""
    return {
        "content": [
            {
                "type": "text",
                "text": _serialize_tool_content(
                    {
                        "error": (
                            "Tool output does not match declared object outputSchema: "
                            f"expected object, got {type(value).__name__}"
                        )
                    }
                ),
            }
        ],
        "isError": True,
    }


def _build_tool_result(
    value: "Any",
    *,
    is_error: "bool",
    max_blob_bytes: "int | None" = None,
    output_schema: "dict[str, Any] | None" = None,
) -> "dict[str, Any]":
    try:
        if isinstance(value, MCPInputRequiredResult):
            return value.to_result()
        if isinstance(value, MCPToolResult):
            result = value.to_result(max_blob_bytes=max_blob_bytes)
            result["isError"] = bool(is_error or result.get("isError", False))
            if (
                not result["isError"]
                and output_schema is not None
                and output_schema.get("type") == "object"
                and "structuredContent" in result
                and not isinstance(result["structuredContent"], dict)
            ):
                result = _non_object_output_error(result["structuredContent"])
        elif not is_error and output_schema is not None and output_schema.get("type") == "object":
            result = (
                {
                    "content": [{"type": "text", "text": _serialize_tool_content(value)}],
                    "structuredContent": value,
                    "isError": False,
                }
                if isinstance(value, dict)
                else _non_object_output_error(value)
            )
        elif _looks_like_tool_content(value):
            result = {
                "content": normalize_content_blocks(value, max_blob_bytes=max_blob_bytes),
                "isError": is_error,
            }
        else:
            result = {
                "content": [{"type": "text", "text": _serialize_tool_content(value)}],
                "isError": is_error,
            }
    except (TypeError, ValueError) as exc:
        result = {
            "content": [{"type": "text", "text": _serialize_tool_content({"error": str(exc)})}],
            "isError": True,
        }
    return result


def _to_pointer(name: "str", msgspec_path: "str") -> "str":
    """Turn ``name`` + ``$.age.limit`` into ``/arguments/age/limit`` JSON Pointer."""
    suffix = msgspec_path.removeprefix("$").lstrip(".")
    parts = ["arguments", name]
    if suffix:
        parts.extend(p for p in suffix.split(".") if p and p != name)
    return "/" + "/".join(parts)


def _split_msgspec_error(exc: "Exception") -> "tuple[str, str]":
    """Split a ``msgspec.ValidationError`` string into (reason, path)."""
    text = str(exc)
    marker = " - at `"
    if marker in text and text.endswith("`"):
        reason, _, tail = text.rpartition(marker)
        path = tail[:-1]
        return reason, path
    return text, ""


def _resolve_annotated_types(handler: "BaseRouteHandler") -> "dict[str, Any]":
    """Return ``{param_name: annotated_type}`` from the original handler function."""
    fn = get_handler_function(handler)
    try:
        return get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001
        return {}


def _validate_tool_arguments(handler: "BaseRouteHandler", tool_args: "dict[str, Any]") -> "list[dict[str, str]]":
    """Validate ``tool_args`` against the handler's Litestar signature.

    Path parameters are excluded since Litestar extracts them directly from
    path variables; remaining scalars are matched against the handler's
    non-DI signature fields.

    Returns a list of ``{"path": <json-pointer>, "message": <reason>}`` dicts,
    sorted by path for deterministic output.
    """

    advertised_params = get_advertised_handler_parameters(handler)
    python_to_wire = {p.python_name: p.wire_name for p in advertised_params}
    resolved_args, consumed, _ = resolve_tool_argument_aliases(tool_args, advertised_params)
    normalized_args = {key: value for key, value in tool_args.items() if key not in consumed}
    normalized_args.update(
        {
            parameter.python_name: resolved_args[parameter.wire_name]
            for parameter in advertised_params
            if parameter.wire_name in resolved_args
        }
    )
    tool_args = normalized_args

    try:
        declared_by_name = dict(handler.parsed_fn_signature.parameters)
    except Exception:  # noqa: BLE001
        return []

    advertised_params = get_advertised_handler_parameters(handler)
    advertised_by_name = {param.python_name: param for param in advertised_params}
    annotated_types = _resolve_annotated_types(handler)
    errors: list[dict[str, str]] = []

    data_field = declared_by_name.get("data")
    data_type = annotated_types.get("data") if data_field is not None else None
    recognized_scalar_names = set(advertised_by_name)

    # When the handler has a ``data`` param, tool_args keys that aren't
    # recognized scalar fields are treated as members of the data struct.
    # Validate them by building a mapping and converting it to the struct.
    if data_type is not None:
        has_explicit_data = "data" in tool_args
        data_payload = (
            tool_args["data"]
            if has_explicit_data
            else {k: v for k, v in tool_args.items() if k not in recognized_scalar_names}
        )
        if has_explicit_data or data_payload:
            try:
                msgspec.convert(data_payload, data_type, strict=False)
            except msgspec.ValidationError as exc:
                reason, path = _split_msgspec_error(exc)
                errors.append({"path": _to_pointer("data", path), "message": reason})
            except TypeError:
                pass

    for name, parameter in advertised_by_name.items():
        if name in tool_args:
            continue
        if name == "data" and data_type is not None:
            data_member_names = set(tool_args) - recognized_scalar_names
            if data_member_names:
                continue
        if parameter.required:
            errors.append({"path": _to_pointer(parameter.wire_name, ""), "message": "Missing required argument"})

    for name, value in tool_args.items():
        if name == "data" and data_type is not None:
            continue
        if name not in recognized_scalar_names:
            if data_type is not None:
                continue
            display_name = python_to_wire.get(name, name)
            errors.append({"path": "/arguments", "message": f"Unexpected argument: {display_name}"})
            continue
        declared = declared_by_name.get(name)
        if declared is not None:
            default_annotation = getattr(declared, "annotation", Any)
        else:
            raw = getattr(advertised_by_name[name], "annotation", Any)
            inner, _ = _unwrap_annotated(raw)
            default_annotation = inner
        convert_type = annotated_types.get(name, default_annotation)
        try:
            msgspec.convert(value, convert_type, strict=False)
        except msgspec.ValidationError as exc:
            reason, path = _split_msgspec_error(exc)
            wire_name = advertised_by_name[name].wire_name
            errors.append({"path": _to_pointer(wire_name, path), "message": reason})
        except TypeError:
            continue

    return sorted(errors, key=lambda entry: (entry["path"], entry["message"]))


class MCPHandlerService:
    """Service class encapsulating the MCP JSON-RPC business logic."""

    def __init__(
        self,
        config: "MCPConfig",
        discovered_tools: "dict[str, BaseRouteHandler]",
        discovered_resources: "dict[str, BaseRouteHandler]",
        discovered_prompts: "dict[str, PromptRegistration]",
        app_ref: "Litestar",
        registry: "Registry | None",
        task_store: "MCPTaskStore | None" = None,
        skill_catalog: "SkillCatalog | None" = None,
    ) -> "None":
        self.config = config
        self.discovered_tools = discovered_tools
        self.discovered_resources = discovered_resources
        self.discovered_prompts = discovered_prompts
        self.app_ref = app_ref
        self.registry = registry
        self.task_store = task_store
        self.task_config = config.task_config
        self.skill_catalog = skill_catalog

    @staticmethod
    def register_routes(
        router: "JSONRPCRouter",
        service_factory: "Callable[[], MCPHandlerService]",
        *,
        task_enabled: "bool",
        skill_catalog: "SkillCatalog | None",
        logging_enabled: "bool" = True,
    ) -> "None":
        """Register supported MCP JSON-RPC methods onto `router`."""
        router.register("server/discover", lambda params, ctx: service_factory().server_discover(params, ctx))
        router.register("initialize", lambda params, ctx: service_factory().initialize(params, ctx))
        router.register("ping", lambda params, ctx: service_factory().ping(params, ctx))
        router.register(
            "notifications/initialized",
            lambda params, ctx: service_factory().notifications_initialized(params, ctx),
        )
        router.register("tools/list", lambda params, ctx: service_factory().tools_list(params, ctx))
        router.register("tools/call", lambda params, ctx: service_factory().tools_call(params, ctx))
        router.register("resources/list", lambda params, ctx: service_factory().resources_list(params, ctx))
        router.register(
            "resources/templates/list",
            lambda params, ctx: service_factory().resources_templates_list(params, ctx),
        )
        router.register("resources/read", lambda params, ctx: service_factory().resources_read(params, ctx))
        router.register(
            "resources/subscribe",
            lambda params, ctx: service_factory().resources_subscribe(params, ctx),
        )
        router.register(
            "resources/unsubscribe",
            lambda params, ctx: service_factory().resources_unsubscribe(params, ctx),
        )
        if logging_enabled:
            router.register(
                "logging/setLevel",
                lambda params, ctx: service_factory().logging_set_level(params, ctx),
            )
        router.register("completion/complete", lambda params, ctx: service_factory().completion_complete(params, ctx))
        router.register("prompts/list", lambda params, ctx: service_factory().prompts_list(params, ctx))
        router.register("prompts/get", lambda params, ctx: service_factory().prompts_get(params, ctx))
        if task_enabled:
            router.register("tasks/get", lambda params, ctx: service_factory().tasks_get(params, ctx))
            router.register("tasks/update", lambda params, ctx: service_factory().tasks_update(params, ctx))
            router.register("tasks/cancel", lambda params, ctx: service_factory().tasks_cancel(params, ctx))
        if skill_catalog is not None:
            router.register("skills/list", lambda params, ctx: service_factory().skills_list(params, ctx))
            router.register("skills/get", lambda params, ctx: service_factory().skills_get(params, ctx))
            if skill_catalog.directory_read:
                router.register(
                    "resources/directory/read",
                    lambda params, ctx: service_factory().resources_directory_read(params, ctx),
                )

    async def _execute_tool_call(
        self,
        tool_name: "str",
        handler: "BaseRouteHandler",
        tool_args: "dict[str, Any]",
        context: "MCPRequestContext",
    ) -> "dict[str, Any]":
        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler, kind="tool") or get_mcp_metadata(fn, kind="tool") or {}
        output_schema = self._resolve_tool_output_schema(handler, metadata)

        validation_errors = _validate_tool_arguments(handler, tool_args)
        if validation_errors:
            return _build_tool_result(
                {"error": "Invalid tool arguments", "errors": validation_errors},
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
                output_schema=output_schema,
            )

        try:
            token = _request_context.set(context)
            try:
                result = await execute_tool(
                    handler,
                    self.app_ref,
                    tool_args,
                    request=context.request,
                    config=self.config,
                    tool_name=tool_name,
                )
            finally:
                _request_context.reset(token)
        except MCPToolErrorResult as err:
            return _build_tool_result(
                err.content,
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
                output_schema=output_schema,
            )
        except JSONRPCErrorException:
            raise
        except Exception as exc:  # noqa: BLE001
            return _build_tool_result(
                {"error": str(exc)},
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
                output_schema=output_schema,
            )

        return _build_tool_result(
            result,
            is_error=False,
            max_blob_bytes=self.config.max_blob_bytes,
            output_schema=output_schema,
        )

    async def _settle_attempt(self, task_id: "str", result: "dict[str, Any]") -> "bool":
        """Settle a tool execution result onto the task store.

        Returns True if input is required, False if complete.
        """
        if self.task_store is None:
            return False
        if result.get("resultType") == "input_required":
            await self.task_store.require_input(
                task_id,
                result.get("inputRequests"),
                result.get("requestState"),
            )
            return True
        result.setdefault("resultType", "complete")
        await self.task_store.complete(task_id, result)
        return False

    async def _run_task(
        self,
        record: "TaskRecord",
        tool_name: "str",
        handler: "BaseRouteHandler",
        tool_args: "dict[str, Any]",
        context: "MCPRequestContext",
    ) -> "None":
        if self.task_store is None:
            return
        try:
            while True:
                if self.config.logging and self.registry is not None and self.registry.has_subscription_manager:
                    self.registry.subscription_manager.set_default_log_level(self.config.default_log_level)
                result = await self._execute_tool_call(tool_name, handler, tool_args, context)
                if not await self._settle_attempt(record.task_id, result):
                    return
                context.input_responses = await self.task_store.wait_for_input(record.task_id)
                context.request_state = result.get("requestState")
                if self.config.logging and context.session_id is not None:
                    context.log_level = await self._resolve_resumed_task_log_level(
                        context.session_id,
                        context.owner_id,
                        context.log_level,
                    )
        except JSONRPCErrorException as exc:
            await self.task_store.fail(record.task_id, exc.error)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                await self.task_store.mark_cancelled(record.task_id)
            raise
        except Exception as exc:  # noqa: BLE001
            await self.task_store.fail(
                record.task_id,
                JSONRPCError(code=INTERNAL_ERROR, message=str(exc)),
                status_message=str(exc),
            )

    def _prepare_user_dependency_call(
        self,
        handler: "BaseRouteHandler",
        request: "Request[Any, Any, Any]",
        call: "ToolTaskCall",
    ) -> "tuple[str, Callable[..., Any], dict[str, Any]] | None":
        """Locate a ``current_user``/``user``/``principal`` dependency and build its invocation kwargs."""
        resolve_deps = getattr(handler, "resolve_dependencies", None)
        dependencies = resolve_deps() if callable(resolve_deps) else None
        if not isinstance(dependencies, Mapping):
            return None

        target_key = next((k for k in ("current_user", "user", "principal") if k in dependencies), None)
        provider = dependencies.get(target_key) if target_key is not None else None
        dep_fn = getattr(provider, "dependency", provider) if provider is not None else None
        if target_key is None or not callable(dep_fn):
            return None

        sig: inspect.Signature | None = None
        with contextlib.suppress(TypeError, ValueError):
            sig = inspect.signature(dep_fn)
        if sig is None:
            return None

        call_kwargs: dict[str, Any] = {}
        raw_owner = call.owner_id.removeprefix("user:") if call.owner_id is not None else None
        for param_name, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if param_name in {"request", "connection"}:
                call_kwargs[param_name] = request
            elif param_name == "state":
                call_kwargs[param_name] = self.app_ref.state
            elif param_name == "scope":
                call_kwargs[param_name] = request.scope
            elif param_name in {"owner_id", "user_id", "principal_id"} and raw_owner is not None:
                call_kwargs[param_name] = raw_owner
            elif param_name not in dependencies and param.default is inspect.Parameter.empty:
                return None

        missing_required = any(
            param.default is inspect.Parameter.empty
            and param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            and name not in call_kwargs
            for name, param in sig.parameters.items()
        )
        return None if missing_required else (target_key, dep_fn, call_kwargs)

    async def _rehydrate_user_from_handler_dependencies(
        self,
        handler: "BaseRouteHandler",
        request: "Request[Any, Any, Any]",
        call: "ToolTaskCall",
    ) -> "None":
        """Hydrate ``request.scope["user"]`` from a handler's ``current_user``/``user``/``principal`` dependency."""
        if call.owner_id is None and call.principal is None:
            return
        prepared = self._prepare_user_dependency_call(handler, request, call)
        if prepared is None:
            return
        target_key, dep_fn, call_kwargs = prepared

        resolved: Any = None
        with contextlib.suppress(Exception):
            resolved = dep_fn(**call_kwargs)
            if inspect.isawaitable(resolved):
                resolved = await resolved
        if resolved is None:
            return

        declared_params: Mapping[str, Any] = {}
        with contextlib.suppress(Exception):
            declared_params = handler.parsed_fn_signature.parameters

        scope = cast("dict[str, Any]", request.scope)
        if target_key in declared_params and isinstance(scope.get("user"), PrincipalProxy):
            extra = extract_principal_snapshot(resolved, owner_id=call.owner_id)
            if isinstance(extra, Mapping):
                merged = dict(scope["user"])
                merged.update({k: v for k, v in extra.items() if not k.startswith("__")})
                scope["user"] = PrincipalProxy(merged)
        else:
            scope["user"] = resolved

    async def run_task_attempt(self, call: "ToolTaskCall", user: "Any") -> "None":
        """Execute one task-augmented tool call attempt in a background worker."""
        if self.task_store is None:
            return
        scope = blank_http_scope(self.app_ref)
        if call.owner_id is not None:
            scope["litestar_mcp.owner_id"] = call.owner_id
        rehydrate_security_scope(scope, call.principal, call.owner_id)
        if user is not None:
            scope["user"] = user
        if self.config.logging and self.registry is not None and self.registry.has_subscription_manager:
            self.registry.subscription_manager.set_default_log_level(self.config.default_log_level)
        request: Request[Any, Any, Any] = Request(cast("Any", scope))
        context = MCPRequestContext(
            client_id=call.client_id,
            owner_id=call.owner_id,
            request=request,
            protocol_version=call.protocol_version,
            client_capabilities=call.client_capabilities,
            client_info=call.client_info,
            input_responses=call.input_responses,
            request_state=call.request_state,
            progress_token=call.progress_token,
            progress_reporter=(
                self._make_task_progress_reporter(call.task_id, call.session_id, owner_id=call.owner_id)
                if call.progress_token is not None
                else None
            ),
            session_id=call.session_id,
            log_level=call.log_level if self.config.logging else None,
            log_reporter=self._make_task_log_reporter(call.session_id, owner_id=call.owner_id),
        )
        handler = self.discovered_tools.get(call.tool_name)
        if handler is None:
            await self.task_store.fail(
                call.task_id,
                JSONRPCError(code=INVALID_PARAMS, message=f"Tool not found: {call.tool_name}"),
            )
            return

        if user is None:
            await self._rehydrate_user_from_handler_dependencies(handler, request, call)

        try:
            result = await self._execute_tool_call(call.tool_name, handler, call.arguments, context)
            await self._settle_attempt(call.task_id, result)
        except JSONRPCErrorException as exc:
            await self.task_store.fail(call.task_id, exc.error)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                await self.task_store.mark_cancelled(call.task_id)
            raise
        except Exception as exc:  # noqa: BLE001
            await self.task_store.fail(
                call.task_id,
                JSONRPCError(code=INTERNAL_ERROR, message=str(exc)),
                status_message=str(exc),
            )

    def _server_capabilities(self, *, protocol_version: "str | None" = None) -> "dict[str, Any]":
        """Build the MCP server capabilities object."""
        resources_capability: dict[str, Any] = (
            {"listChanged": True}
            if protocol_version == MCP_PROTOCOL_VERSION
            else {"subscribe": True, "listChanged": True}
        )
        capabilities: dict[str, Any] = {
            "tools": {"listChanged": True},
            "resources": resources_capability,
        }
        if self.config.logging:
            capabilities["logging"] = {}
        if self.discovered_prompts or (self.skill_catalog is not None and self.skill_catalog.expose_as_prompts):
            capabilities["prompts"] = {"listChanged": True}
        extensions: dict[str, Any] = {}
        if self.task_config is not None:
            extensions[TASKS_EXTENSION] = {}
        if self.skill_catalog is not None:
            extensions[SKILLS_EXTENSION] = {
                "directoryRead": self.skill_catalog.directory_read,
                "listChanged": self.skill_catalog.list_changed,
            }
        ui_mime_types = self._resolved_ui_mime_types()
        if ui_mime_types:
            extensions[UI_EXTENSION] = {"mimeTypes": ui_mime_types}
        if extensions:
            capabilities["extensions"] = extensions
        return capabilities

    def _server_info(self) -> "dict[str, str]":
        """Return server name and version derived from MCPConfig and Litestar OpenAPI config."""
        openapi = getattr(self.app_ref, "openapi_config", None)
        return {
            "name": self.config.name or (openapi.title if openapi is not None else "Litestar MCP Server"),
            "version": openapi.version if openapi is not None else "1.0.0",
        }

    async def server_discover(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Describe the modern protocol versions, capabilities, and extensions."""
        result: dict[str, Any] = {
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": self._server_capabilities(protocol_version=MCP_PROTOCOL_VERSION),
        }
        if self.config.instructions is not None:
            result["instructions"] = self.config.instructions
        return result

    async def initialize(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Negotiate MCP protocol version and return server capabilities and info."""
        requested = params.get("protocolVersion")
        negotiated = (
            requested if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS else "2025-11-25"
        )
        result: dict[str, Any] = {
            "protocolVersion": negotiated,
            "capabilities": self._server_capabilities(protocol_version=negotiated),
            "serverInfo": self._server_info(),
        }
        if self.config.instructions is not None:
            result["instructions"] = self.config.instructions
        return result

    async def ping(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Acknowledge an MCP ping health check."""
        return {}

    async def notifications_initialized(
        self, params: "dict[str, Any]", context: "MCPRequestContext"
    ) -> "dict[str, Any]":
        """Acknowledge an initialized notification."""
        return {}

    def _infer_http_method_annotations(self, handler: "BaseRouteHandler") -> "dict[str, Any]":
        """Infer default MCP ToolAnnotations hints from a route handler's HTTP method."""
        raw_methods = getattr(handler, "http_methods", None)
        if not raw_methods:
            single = getattr(handler, "http_method", None)
            raw_methods = {single} if single else set()
        methods: set[str] = set()
        for method in raw_methods if isinstance(raw_methods, (set, frozenset, list, tuple)) else (raw_methods,):
            val = getattr(method, "value", method)
            if isinstance(val, str) and val:
                methods.add(val.upper())
        if not methods:
            return {}
        if methods <= {"GET", "HEAD"}:
            return {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
        if methods == {"PUT"}:
            return {"readOnlyHint": False, "idempotentHint": True}
        if methods == {"DELETE"}:
            return {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}
        if methods <= {"POST", "PATCH"}:
            return {"readOnlyHint": False}
        return {}

    def _resolve_tool_annotations(
        self,
        handler: "BaseRouteHandler",
        metadata: "Mapping[str, Any]",
    ) -> "dict[str, Any]":
        """Resolve effective MCP ToolAnnotations for a route handler."""
        handler_opt = getattr(handler, "opt", None) or {}
        opt_keys = self.config.opt_keys
        annotations: dict[str, Any] = {}
        if self.config.infer_http_method_annotations and not handler_opt.get("_mcp_synthesized_tool"):
            annotations.update(self._infer_http_method_annotations(handler))

        meta_annotations = metadata.get("annotations")
        if isinstance(meta_annotations, Mapping):
            annotations.update(meta_annotations)

        opt_annotations = handler_opt.get(opt_keys.annotations)
        if isinstance(opt_annotations, Mapping):
            annotations.update(opt_annotations)

        if metadata.get("read_only") is not None:
            annotations["readOnlyHint"] = bool(metadata["read_only"])

        opt_read_only = handler_opt.get(opt_keys.read_only)
        if opt_read_only is not None:
            annotations["readOnlyHint"] = bool(opt_read_only)

        if "scopes" in metadata and "scopes" not in annotations:
            annotations["scopes"] = list(metadata["scopes"])

        return annotations

    def _tool_display_name(self, handler: "BaseRouteHandler", metadata: "Mapping[str, Any]") -> "str":
        """Return a stable tool identifier for configuration error messages."""
        meta_name = metadata.get("name")
        if isinstance(meta_name, str) and meta_name:
            return meta_name
        handler_opt = getattr(handler, "opt", None) or {}
        opt_tool = handler_opt.get(self.config.opt_keys.tool)
        if isinstance(opt_tool, str) and opt_tool:
            return opt_tool
        handler_name = getattr(handler, "name", None)
        if isinstance(handler_name, str) and handler_name:
            return handler_name
        fn = get_handler_function(handler) if hasattr(handler, "fn") else None
        fn_name = getattr(fn, "__name__", None)
        return fn_name if isinstance(fn_name, str) and fn_name else "tool"

    def _resolve_tool_output_schema(
        self,
        handler: "BaseRouteHandler",
        metadata: "Mapping[str, Any]",
    ) -> "dict[str, Any] | None":
        """Resolve effective MCP `outputSchema` across `handler.opt`, decorator metadata, and `MCPConfig`."""
        handler_opt = getattr(handler, "opt", None) or {}
        opt_key = self.config.opt_keys.output_schema
        if opt_key in handler_opt and handler_opt[opt_key] is not None:
            policy: Any = handler_opt[opt_key]
        elif "output_schema" in metadata and metadata["output_schema"] is not None:
            policy = metadata["output_schema"]
        else:
            policy = self.config.auto_output_schema

        if policy is False or policy is None:
            return None
        if policy is True:
            schema = cached_output_schema_for_handler(handler)
            return copy.deepcopy(schema) if schema is not None else None
        if isinstance(policy, Mapping):
            resolved = unwrap_root_object_schema(dict(policy))
            if resolved.get("type") != "object":
                label = self._tool_display_name(handler, metadata)
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message=f"Tool '{label}' output_schema must resolve to root type 'object'",
                    )
                )
            return resolved
        label = self._tool_display_name(handler, metadata)
        raise JSONRPCErrorException(
            JSONRPCError(
                code=INTERNAL_ERROR,
                message=f"Tool '{label}' has invalid output_schema policy of type {type(policy).__name__}",
            )
        )

    def build_tool_entries(self) -> "list[dict[str, Any]]":
        """Build the unpaginated list of advertised MCP tool specifications."""
        tools: list[dict[str, Any]] = []
        for name, handler in sorted(self.discovered_tools.items()):
            if not should_include_handler(name, _handler_tags(handler), self.config):
                continue

            fn = get_handler_function(handler)
            metadata = get_mcp_metadata(handler, kind="tool") or get_mcp_metadata(fn, kind="tool") or {}
            tool_entry: dict[str, Any] = {
                "name": name,
                "description": render_description(
                    handler, fn, kind="tool", fallback_name=name, opt_keys=self.config.opt_keys
                ),
                "inputSchema": metadata["input_schema"]
                if "input_schema" in metadata
                else cached_schema_for_handler(handler),
            }
            output_schema = self._resolve_tool_output_schema(handler, metadata)
            if output_schema is not None:
                tool_entry["outputSchema"] = output_schema
            annotations = self._resolve_tool_annotations(handler, metadata)
            if annotations:
                tool_entry["annotations"] = annotations
            title, icons, meta = self._resolve_descriptor_fields(handler, kind="tool")
            _apply_descriptor_fields(tool_entry, title=title, icons=icons, meta=meta)
            tools.append(tool_entry)
        return tools

    async def tools_list(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Return a paginated list of registered MCP tools matching active filters."""
        tools = self.build_tool_entries()
        try:
            page, next_cursor = _paginate_list(tools, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"tools": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    def _resolve_tool_task_support(
        self,
        handler: "BaseRouteHandler",
        metadata: "Mapping[str, Any]",
    ) -> "str | None":
        """Resolve effective MCP task support mode across decorator metadata and route/controller/router opt."""
        raw_meta = metadata.get("task_support") if metadata.get("task_support") is not None else metadata.get("task")
        if raw_meta is not None:
            return normalize_task_support(raw_meta)
        handler_opt = getattr(handler, "opt", None) or {}
        opt_keys = self.config.opt_keys
        raw_opt = (
            handler_opt.get(opt_keys.task) if opt_keys.task in handler_opt else handler_opt.get(opt_keys.task_support)
        )
        return normalize_task_support(raw_opt)

    def _resolve_tool_queue(
        self,
        handler: "BaseRouteHandler",
        metadata: "Mapping[str, Any]",
    ) -> "str | bool | None":
        """Resolve effective queue override across decorator metadata and route/controller/router opt."""
        if "queue" in metadata and metadata["queue"] is not None:
            return cast("str | bool | None", metadata["queue"])
        handler_opt = getattr(handler, "opt", None) or {}
        return cast("str | bool | None", handler_opt.get(self.config.opt_keys.queue))

    async def tools_call(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Invoke a registered MCP tool synchronously or promote it to a background task."""
        tool_name = params.get("name")
        if not tool_name:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'"))

        handler = self.discovered_tools.get(tool_name)
        if handler is None:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Tool not found: {tool_name}"))
        if not should_include_handler(tool_name, _handler_tags(handler), self.config):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Tool not found: {tool_name}"))

        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler, kind="tool") or get_mcp_metadata(fn, kind="tool") or {}
        handler_opt = getattr(handler, "opt", None) or {}
        required_capabilities = metadata.get("required_client_capabilities") or handler_opt.get(
            self.config.opt_keys.required_client_capabilities
        )
        if required_capabilities is not None:
            if not isinstance(required_capabilities, dict):
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message=f"Tool '{tool_name}' has invalid required client capability metadata",
                    )
                )
            client_capabilities = context.client_capabilities or {}
            missing_capabilities = {
                name: declaration
                for name, declaration in required_capabilities.items()
                if name not in client_capabilities
            }
            if missing_capabilities:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                        message=f"Tool '{tool_name}' requires additional client capabilities",
                        data={"requiredCapabilities": missing_capabilities},
                    )
                )
        tool_args = params.get("arguments", {})
        if not isinstance(tool_args, dict):
            return _build_tool_result(
                {"error": "Tool arguments must be an object"},
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
            )

        task_support = self._resolve_tool_task_support(handler, metadata)
        extensions = (context.client_capabilities or {}).get("extensions", {})
        task_capable = isinstance(extensions, dict) and TASKS_EXTENSION in extensions
        task_enabled = self.task_config is not None and self.task_store is not None
        if task_support == "required" and not (task_enabled and task_capable):
            raise JSONRPCErrorException(
                JSONRPCError(
                    code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                    message=f"Tool '{tool_name}' requires the {TASKS_EXTENSION} client capability",
                    data={"requiredCapabilities": {"extensions": {TASKS_EXTENSION: {}}}},
                )
            )
        if task_support not in {"optional", "required"} or not task_enabled or not task_capable:
            return await self._execute_tool_call(tool_name, handler, tool_args, context)
        task_input_before_start = metadata.get("task_input_before_start") or handler_opt.get(
            self.config.opt_keys.task_input_before_start
        )
        if task_input_before_start and context.input_responses is None:
            return await self._execute_tool_call(tool_name, handler, tool_args, context)

        task_store = self.task_store
        if task_store is None:  # pragma: no cover - narrowed by task_enabled
            return await self._execute_tool_call(tool_name, handler, tool_args, context)

        route_queue = self._resolve_tool_queue(handler, metadata)
        force_in_process = route_queue in (False, "in_process")
        raw_executor = None if force_in_process or self.task_config is None else self.task_config.executor
        executor = None if isinstance(raw_executor, (bool, str)) else raw_executor

        if executor is not None:
            req_scope = cast("dict[str, Any]", context.request.scope) if context.request is not None else {}
            principal_snapshot = extract_principal_snapshot(
                req_scope.get("user"),
                req_scope.get("auth"),
                req_scope.get("litestar_security"),
                context.owner_id,
            )
            target_queue = route_queue if isinstance(route_queue, str) and route_queue else None
            call = ToolTaskCall(
                task_id="",
                tool_name=tool_name,
                arguments=tool_args,
                owner_id=context.owner_id,
                principal=principal_snapshot,
                queue=target_queue,
                client_id=context.client_id,
                protocol_version=context.protocol_version,
                client_capabilities=context.client_capabilities,
                client_info=context.client_info,
                input_responses=context.input_responses,
                request_state=context.request_state,
                progress_token=context.progress_token,
                session_id=context.session_id,
                log_level=context.log_level,
            )
            record = await task_store.create(context.owner_id, local=False)
            call = msgspec.structs.replace(call, task_id=record.task_id)
            await task_store.set_dispatch(record.task_id, call)
            try:
                await executor.submit(record, call)
            except Exception as exc:
                raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message=str(exc))) from exc
            return {"resultType": "task", **record.to_dict()}

        record = await task_store.create(context.owner_id)
        bg_request = context.request
        if bg_request is not None:
            bg_scope = dict(cast("dict[str, Any]", bg_request.scope))
            bg_state = dict(cast("dict[str, Any]", bg_scope.get("state") or {}))
            bg_state.pop("dishka_container", None)
            bg_scope["state"] = bg_state
            bg_request = Request(cast("Any", bg_scope))
        bg_context = replace(
            context,
            request=bg_request,
            progress_reporter=(
                self._make_task_progress_reporter(record.task_id, context.session_id, owner_id=context.owner_id)
                if context.progress_token is not None
                else None
            ),
            log_reporter=self._make_task_log_reporter(context.session_id, owner_id=context.owner_id),
        )
        background_task = asyncio.create_task(self._run_task(record, tool_name, handler, tool_args, bg_context))
        await task_store.attach_runner(record.task_id, background_task)
        return {"resultType": "task", **record.to_dict()}

    async def resources_list(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Return a paginated list of concrete MCP resources and visible skill files."""
        resources = [
            {
                "uri": "litestar://openapi",
                "name": "openapi",
                "description": "OpenAPI schema for this Litestar application",
                "mimeType": "application/json",
            }
        ]
        for name, handler in self.discovered_resources.items():
            if not should_include_handler(name, _handler_tags(handler), self.config):
                continue

            fn = get_handler_function(handler)
            entry: dict[str, Any] = {
                "uri": _resource_uri(name, handler, self.config),
                "name": name,
                "description": render_description(
                    handler,
                    fn,
                    kind="resource",
                    fallback_name=name,
                    opt_keys=self.config.opt_keys,
                ),
                "mimeType": _resource_mime_type(handler, self.config),
            }
            title, icons, meta = self._resolve_descriptor_fields(handler, kind="resource")
            _apply_descriptor_fields(entry, title=title, icons=icons, meta=meta)
            resources.append(entry)
        if self.skill_catalog is not None:
            token = _request_context.set(context)
            try:
                for skill in await self.skill_catalog.list_skills(context.request):
                    if self._should_include_skill(skill):
                        resources.extend(skill.to_resources())
            finally:
                _request_context.reset(token)
        try:
            page, next_cursor = _paginate_list(resources, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"resources": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def resources_templates_list(
        self, params: "dict[str, Any]", context: "MCPRequestContext"
    ) -> "dict[str, Any]":
        """Return a paginated list of registered RFC 6570 resource URI templates."""
        if self.registry is None:
            return {"resourceTemplates": []}
        templates = []
        for entry in self.registry.templates.values():
            if not should_include_handler(entry.name, _handler_tags(entry.handler), self.config):
                continue
            fn = get_handler_function(entry.handler)
            template_entry: dict[str, Any] = {
                "uriTemplate": entry.template,
                "name": entry.name,
                "description": render_description(
                    entry.handler,
                    fn,
                    kind="resource",
                    fallback_name=entry.name,
                    opt_keys=self.config.opt_keys,
                ),
                "mimeType": _resource_mime_type(entry.handler, self.config),
            }
            title, icons, meta = self._resolve_descriptor_fields(entry.handler, kind="resource", template_entry=entry)
            _apply_descriptor_fields(template_entry, title=title, icons=icons, meta=meta)
            templates.append(template_entry)
        try:
            page, next_cursor = _paginate_list(templates, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"resourceTemplates": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def resources_read(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Read the contents of a concrete resource, skill file, or URI-template resource."""
        uri = params.get("uri", "")
        if not isinstance(uri, str) or not uri:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Invalid resource URI: {uri}"))

        if uri == "litestar://openapi" and self.app_ref is not None:
            return {
                "contents": [
                    {
                        "uri": "litestar://openapi",
                        "mimeType": "application/json",
                        "text": encode_json(_without_undefined_values(self.app_ref.openapi_schema.to_schema())).decode(
                            "utf-8"
                        ),
                    }
                ]
            }

        if self.skill_catalog is not None and uri.startswith(SKILL_URI_PREFIX):
            token = _request_context.set(context)
            try:
                match = await self.skill_catalog.get_file(context.request, uri)
                if match is None or not self._should_include_skill(match.skill):
                    raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))
                skill_file = match.file
                if not _is_resource_text_media_type(skill_file.mime_type):
                    try:
                        enforce_blob_size(skill_file.size, max_blob_bytes=self.config.max_blob_bytes)
                    except ValueError as exc:
                        raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc
                try:
                    body = await self.skill_catalog.read_file(context.request, match)
                except SkillIntegrityError as exc:
                    raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc
                except LookupError as exc:
                    raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri)) from exc
                except OSError as exc:
                    _logger.exception("Skill file read failed: %s", uri)
                    raise JSONRPCErrorException(mcp_error_for_skill_file_unreadable(uri)) from exc
            finally:
                _request_context.reset(token)
            try:
                content = _resource_content_from_bytes(
                    uri, body, mime_type=skill_file.mime_type, max_blob_bytes=self.config.max_blob_bytes
                )
            except ValueError as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc
            meta = (
                skill_file.meta
                if skill_file.meta is not None
                else (match.skill.meta if skill_file.relative_path == "SKILL.md" else None)
            )
            if meta:
                content["_meta"] = dict(meta)
            return {"contents": [content]}

        resource_match = next(
            (
                (name, handler)
                for name, handler in self.discovered_resources.items()
                if _resource_uri(name, handler, self.config) == uri
            ),
            None,
        )
        if resource_match is not None:
            resource_name, handler = resource_match
            if not should_include_handler(resource_name, _handler_tags(handler), self.config):
                raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))

            try:
                token = _request_context.set(context)
                try:
                    response = await execute_handler_response(
                        handler,
                        self.app_ref,
                        {},
                        request=context.request,
                    )
                finally:
                    _request_context.reset(token)
            except MCPToolErrorResult as err:
                raise JSONRPCErrorException(mcp_error_for_resource_read(err)) from err
            except Exception as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            if isinstance(response.content, MCPInputRequiredResult):
                return response.content.to_result()
            try:
                content = _resource_content_from_response(
                    uri,
                    response,
                    fallback_mime_type=_resource_mime_type(handler, self.config),
                    explicit_mime_type=_explicit_resource_mime_type(handler, self.config),
                    handler_media_type=_handler_media_type(handler),
                    max_blob_bytes=self.config.max_blob_bytes,
                )
            except ValueError as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            _, _, meta = self._resolve_descriptor_fields(handler, kind="resource")
            if meta is not None:
                content["_meta"] = meta
            return {"contents": [content]}

        template_entries = self.registry.templates.values() if self.registry is not None else ()
        for entry in template_entries:
            extracted = match_uri(entry.template, uri)
            if extracted is None:
                continue
            if not should_include_handler(entry.name, _handler_tags(entry.handler), self.config):
                continue
            try:
                token = _request_context.set(context)
                try:
                    response = await execute_handler_response(
                        entry.handler,
                        self.app_ref,
                        dict(extracted),
                        request=context.request,
                    )
                finally:
                    _request_context.reset(token)
            except MCPToolErrorResult as err:
                raise JSONRPCErrorException(mcp_error_for_resource_read(err)) from err
            except Exception as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            if isinstance(response.content, MCPInputRequiredResult):
                return response.content.to_result()
            try:
                content = _resource_content_from_response(
                    uri,
                    response,
                    fallback_mime_type=_resource_mime_type(entry.handler, self.config),
                    explicit_mime_type=_explicit_resource_mime_type(entry.handler, self.config),
                    handler_media_type=_handler_media_type(entry.handler),
                    max_blob_bytes=self.config.max_blob_bytes,
                )
            except ValueError as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            _, _, meta = self._resolve_descriptor_fields(entry.handler, kind="resource", template_entry=entry)
            if meta is not None:
                content["_meta"] = meta
            return {"contents": [content]}

        raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))

    async def resources_subscribe(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Subscribe the active `2025-*` MCP session to change notifications for `uri`."""
        uri = _validate_subscription_uri(params.get("uri"))
        if context.session_id is None:
            raise JSONRPCErrorException(
                JSONRPCError(
                    code=INVALID_PARAMS,
                    message="resources/subscribe requires an active Mcp-Session-Id session",
                )
            )
        if self.config.subscription_authorizer is not None:
            if context.request is None:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message="Subscription authorization requires an HTTP request context",
                    )
                )
            outcome = self.config.subscription_authorizer(context.request, uri)
            allowed = bool(await outcome) if inspect.isawaitable(outcome) else bool(outcome)
            if not allowed:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=f"Not authorized to subscribe to resource: {uri}",
                    )
                )
        session = await get_session_store(self.app_ref, self.config).add_subscription(
            context.session_id,
            context.owner_id,
            uri,
        )
        if session is None:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Session not found or expired"))
        if self.registry is not None and self.registry.has_subscription_manager:
            await self.registry.subscription_manager.add_resource_subscription(context.session_id, uri)
        return {}

    async def resources_unsubscribe(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Unsubscribe the active `2025-*` MCP session from change notifications for `uri`."""
        uri = _validate_subscription_uri(params.get("uri"))
        if context.session_id is None:
            raise JSONRPCErrorException(
                JSONRPCError(
                    code=INVALID_PARAMS,
                    message="resources/unsubscribe requires an active Mcp-Session-Id session",
                )
            )
        session = await get_session_store(self.app_ref, self.config).remove_subscription(
            context.session_id,
            context.owner_id,
            uri,
        )
        if session is None:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Session not found or expired"))
        if self.registry is not None and self.registry.has_subscription_manager:
            await self.registry.subscription_manager.remove_resource_subscription(context.session_id, uri)
        return {}

    async def logging_set_level(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Set the active minimum RFC 5424 log severity for the session or validate statelessly."""
        if not self.config.logging:
            raise JSONRPCErrorException(
                JSONRPCError(code=METHOD_NOT_FOUND, message="Method not found: logging/setLevel")
            )
        level = params.get("level")
        if not isinstance(level, str) or level not in LOG_LEVEL_SEVERITY:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Invalid logging level: {level!r}"))
        if context.session_id is not None:
            session = await get_session_store(self.app_ref, self.config).set_log_level(
                context.session_id,
                context.owner_id,
                level,
            )
            if session is None:
                raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Session not found or expired"))
            if self.registry is not None and self.registry.has_subscription_manager:
                self.registry.subscription_manager.set_session_log_level(context.session_id, level)
        return {}

    async def skills_list(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """List every skill in the catalog matching active filters, one atomic entry per skill."""
        if self.skill_catalog is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Skills are not configured"))
        raw_tags = params.get("tags")
        if raw_tags is not None and (
            not isinstance(raw_tags, list) or not all(isinstance(tag, str) for tag in raw_tags)
        ):
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message="Param 'tags' must be a list of strings")
            )
        raw_query = params.get("query")
        if raw_query is not None and not isinstance(raw_query, str):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Param 'query' must be a string"))
        token = _request_context.set(context)
        try:
            skills = await self.skill_catalog.list_skills(context.request)
        finally:
            _request_context.reset(token)
        visible_skills = [skill for skill in skills if self._should_include_skill(skill)]
        filtered_skills = filter_skills(visible_skills, tags=raw_tags, query=raw_query)
        entries = [skill.to_entry() for skill in filtered_skills]
        try:
            page, next_cursor = _paginate_list(entries, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"skills": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def skills_get(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Return one skill's manifest by its ``skill://`` URI."""
        if self.skill_catalog is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Skills are not configured"))
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'uri'"))
        token = _request_context.set(context)
        try:
            skill = await self.skill_catalog.get(context.request, uri)
        finally:
            _request_context.reset(token)
        if skill is None or not self._should_include_skill(skill):
            raise JSONRPCErrorException(mcp_error_for_skill_not_found(uri))
        return {"skill": skill.to_entry()}

    async def resources_directory_read(
        self, params: "dict[str, Any]", context: "MCPRequestContext"
    ) -> "dict[str, Any]":
        """List the files and subdirectories directly under a ``skill://`` directory URI."""
        if self.skill_catalog is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Skills are not configured"))
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'uri'"))
        parsed = parse_skill_uri(uri)
        if parsed is None:
            raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))
        skill_name, relative_path = parsed
        token = _request_context.set(context)
        try:
            skill = await self.skill_catalog.get_skill(context.request, skill_name)
            if skill is None or not self._should_include_skill(skill):
                raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))
            children = skill.list_directory(relative_path)
        finally:
            _request_context.reset(token)
        if children is None:
            raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))
        try:
            page, next_cursor = _paginate_list(children, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"resources": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def completion_complete(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Return argument auto-completion suggestions for prompts or resource templates."""
        return {"completion": {"values": [], "total": 0, "hasMore": False}}

    async def prompts_list(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Return a paginated list of registered and projected MCP prompts matching active filters."""
        prompts = [
            render_prompt_entry(registration, self.config)
            for registration in self.discovered_prompts.values()
            if should_include_prompt(registration, self.config)
        ]
        if self.skill_catalog is not None and self.skill_catalog.expose_as_prompts:
            token = _request_context.set(context)
            try:
                skills = await self.skill_catalog.list_skills(context.request)
            finally:
                _request_context.reset(token)
            for skill in skills:
                if skill.name in self.discovered_prompts:
                    continue
                if not self._should_include_skill(skill):
                    continue
                skill_prompt: dict[str, Any] = {
                    "name": skill.name,
                    "description": skill.description,
                }
                if skill.title is not None:
                    skill_prompt["title"] = skill.title
                if skill.icons:
                    skill_prompt["icons"] = [dict(icon) for icon in skill.icons]
                if skill.meta:
                    skill_prompt["_meta"] = dict(skill.meta)
                prompts.append(skill_prompt)
        try:
            page, next_cursor = _paginate_list(prompts, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"prompts": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def prompts_get(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Render a registered or skill-projected MCP prompt with caller-supplied string arguments."""
        prompt_name = params.get("name")
        if not prompt_name:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'"))

        registration = self.discovered_prompts.get(prompt_name)
        if registration is None:
            if self.skill_catalog is not None and self.skill_catalog.expose_as_prompts:
                return await self._get_skill_prompt(prompt_name, params, context, self.skill_catalog)
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Prompt not found: {prompt_name}"))
        if not should_include_prompt(registration, self.config):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Prompt not found: {prompt_name}"))

        prompt_args = params.get("arguments", {})
        if not isinstance(prompt_args, dict):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Prompt arguments must be an object"))

        for arg_key, arg_val in prompt_args.items():
            if not isinstance(arg_val, str):
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=f"Argument '{arg_key}' must be a string, got {type(arg_val).__name__}",
                    )
                )

        resolved_description = resolve_prompt_description(registration, self.config)

        if registration.handler is not None:
            declared_args = registration.get_arguments()
            declared_names = {arg["name"] for arg in declared_args}
            missing = [arg["name"] for arg in declared_args if arg.get("required") and arg["name"] not in prompt_args]
            if missing:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=f"Missing required prompt argument(s): {', '.join(sorted(missing))}",
                    )
                )
            unknown = [name for name in prompt_args if name not in declared_names]
            if unknown:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=(
                            f"Unknown prompt argument(s): {', '.join(sorted(unknown))}. "
                            "Prompt handlers should declare scalar arguments only; "
                            "any 'data' body parameter on the handler is ignored by prompts/get."
                        ),
                    )
                )

            try:
                token = _request_context.set(context)
                try:
                    result = await execute_handler(
                        registration.handler,
                        self.app_ref,
                        prompt_args,
                        request=context.request,
                    )
                finally:
                    _request_context.reset(token)
            except MCPToolErrorResult as err:
                _logger.warning(
                    "Prompt handler returned error result: %s (status=%d)",
                    prompt_name,
                    err.status_code,
                )
                raise JSONRPCErrorException(mcp_error_for_prompt_execution(err)) from err
            except JSONRPCErrorException:
                raise
            except Exception as exc:
                _logger.exception("Prompt handler execution failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message="Prompt execution failed",
                        data={"error": type(exc).__name__, "detail": str(exc)},
                    )
                ) from exc
            if isinstance(result, MCPInputRequiredResult):
                return result.to_result()
            handler_result: dict[str, Any]
            if isinstance(result, dict) and "messages" in result:
                handler_result = result
            else:
                handler_result = {"messages": _normalize_prompt_result(result)}
            handler_result["messages"] = apply_prompt_instructions(
                handler_result["messages"],
                registration.instructions,
            )
            if resolved_description is not None and "description" not in handler_result:
                handler_result["description"] = resolved_description
            return handler_result

        if registration.fn is not None:
            try:
                inspect.signature(registration.fn).bind(**prompt_args)
            except TypeError as exc:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Invalid prompt arguments: {exc!s}")
                ) from exc

            try:
                token = _request_context.set(context)
                try:
                    result = registration.fn(**prompt_args)
                    if inspect.isawaitable(result):
                        result = await result
                finally:
                    _request_context.reset(token)
            except Exception as exc:
                _logger.exception("Prompt function execution failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message="Prompt execution failed",
                        data={"error": type(exc).__name__, "detail": str(exc)},
                    )
                ) from exc
            if isinstance(result, MCPInputRequiredResult):
                return result.to_result()
            messages = _normalize_prompt_result(result)
            messages = apply_prompt_instructions(messages, registration.instructions)
            get_result: dict[str, Any] = {"messages": messages}
            if resolved_description is not None and "description" not in get_result:
                get_result["description"] = resolved_description
            return get_result

        raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message=f"Prompt has no callable: {prompt_name}"))

    async def tasks_get(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Retrieve the current status and result of a background MCP task."""
        _require_tasks_capability(context)
        if self.task_store is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Task store not configured"))
        task_id = params.get("taskId")
        if not task_id:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'"))
        try:
            record = await self.task_store.get(task_id, context.owner_id)
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        return record.to_dict()

    async def tasks_update(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Supply interactive input responses to a task awaiting client input and resume execution."""
        _require_tasks_capability(context)
        if self.task_store is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Task store not configured"))
        task_id = params.get("taskId")
        if not task_id:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'"))
        input_responses = params.get("inputResponses")
        if not isinstance(input_responses, dict):
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message="The 'inputResponses' parameter must be an object")
            )
        try:
            responses = await self.task_store.update(task_id, context.owner_id, input_responses)
            raw_executor = self.task_config.executor if self.task_config is not None else None
            executor = None if isinstance(raw_executor, (bool, str)) else raw_executor
            if responses is not None and executor is not None and not self.task_store.has_local_runner(task_id):
                record = await self.task_store.get(task_id, context.owner_id)
                dispatch = await self.task_store.get_dispatch(task_id)
                if dispatch is None:
                    await self.task_store.fail(
                        task_id, JSONRPCError(code=INTERNAL_ERROR, message="Task dispatch is missing")
                    )
                else:
                    merged_responses = dict(dispatch.input_responses or {})
                    merged_responses.update(responses)
                    refreshed_log_level = (
                        await self._resolve_resumed_task_log_level(
                            dispatch.session_id,
                            dispatch.owner_id,
                            dispatch.log_level,
                        )
                        if self.config.logging and dispatch.session_id is not None
                        else dispatch.log_level
                    )
                    call = msgspec.structs.replace(
                        dispatch,
                        input_responses=merged_responses,
                        request_state=record.request_state,
                        attempt=dispatch.attempt + 1,
                        log_level=refreshed_log_level,
                    )
                    await self.task_store.set_dispatch(task_id, call)
                    await executor.resume(call)
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        return {}

    async def tasks_cancel(self, params: "dict[str, Any]", context: "MCPRequestContext") -> "dict[str, Any]":
        """Request cancellation of an in-flight MCP background task."""
        _require_tasks_capability(context)
        if self.task_store is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Task store not configured"))
        task_id = params.get("taskId")
        if not task_id:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'"))
        try:
            raw_executor = self.task_config.executor if self.task_config is not None else None
            executor = None if isinstance(raw_executor, (bool, str)) else raw_executor
            if executor is not None and not self.task_store.has_local_runner(task_id):
                record = await self.task_store.get(task_id, context.owner_id)
                await executor.cancel(record)
            else:
                await self.task_store.cancel(task_id, context.owner_id)
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        return {}

    def _make_task_progress_reporter(
        self,
        task_id: "str",
        session_id: "str | None",
        owner_id: "str | None" = None,
    ) -> "Callable[[dict[str, Any]], Awaitable[None]]":
        """Build a background progress reporter updating task `status_message` and session SSE streams."""

        async def _report(params: "dict[str, Any]") -> "None":
            message = params.get("message")
            if self.task_store is not None and isinstance(message, str):
                await self.task_store.update_status_message(task_id, message)
            if session_id is not None and self.registry is not None and self.registry.has_subscription_manager:
                await self.registry.subscription_manager.publish(
                    "notifications/progress",
                    params,
                    owner_id=owner_id,
                    session_id=session_id,
                )

        return _report

    def _make_task_log_reporter(
        self,
        session_id: "str | None",
        owner_id: "str | None" = None,
    ) -> "Callable[[str, Any, str | None], Awaitable[None]] | None":
        """Build a background log reporter publishing `notifications/message` entries when logging is enabled."""
        if not self.config.logging:
            return None

        async def _report_log(level: "str", data: "Any", logger_name: "str | None") -> "None":
            if self.registry is None or not self.registry.has_subscription_manager:
                return
            payload: dict[str, Any] = {"level": level, "data": data}
            if logger_name is not None:
                payload["logger"] = logger_name
            await self.registry.subscription_manager.publish(
                "notifications/message",
                payload,
                owner_id=owner_id,
                session_id=session_id,
            )

        return _report_log

    async def _resolve_resumed_task_log_level(
        self,
        session_id: "str",
        owner_id: "str | None",
        current_log_level: "str | None",
    ) -> "str | None":
        """Resolve the latest session log severity threshold from `MCPSessionStore` or `SubscriptionManager`."""
        session = await get_session_store(self.app_ref, self.config).get(session_id, owner_id)
        if session is not None and session.log_level is not None and session.log_level in LOG_LEVEL_SEVERITY:
            if self.registry is not None and self.registry.has_subscription_manager:
                self.registry.subscription_manager.set_session_log_level(session_id, session.log_level)
            return session.log_level
        if self.registry is not None and self.registry.has_subscription_manager:
            fallback = (
                current_log_level
                if current_log_level is not None and current_log_level in LOG_LEVEL_SEVERITY
                else self.config.default_log_level
            )
            return self.registry.subscription_manager.get_session_log_level(session_id, default=fallback)
        return current_log_level

    def _resolve_descriptor_fields(
        self,
        handler: "BaseRouteHandler",
        *,
        kind: "Kind",
        template_entry: "ResourceTemplate | None" = None,
    ) -> "tuple[str | None, list[dict[str, Any]] | None, dict[str, Any] | None]":
        """Resolve normalized `(title, icons, meta)` descriptors for a tool or resource handler."""
        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler, kind=kind) or get_mcp_metadata(fn, kind=kind) or {}
        raw_opt: Any = getattr(handler, "opt", None)
        handler_opt: Mapping[str, Any] = raw_opt if isinstance(raw_opt, Mapping) else {}
        opt_keys = self.config.opt_keys

        title = (
            _clean_title(handler_opt.get(opt_keys.for_field("title", kind)))
            or _clean_title(getattr(template_entry, "title", None))
            or _clean_title(metadata.get("title"))
        )
        icons = (
            _clean_icons(handler_opt.get(opt_keys.for_field("icons", kind)))
            or _clean_icons(getattr(template_entry, "icons", None))
            or _clean_icons(metadata.get("icons"))
        )
        merged_meta: dict[str, Any] = {}
        for candidate in (
            _clean_meta(metadata.get("meta")),
            _clean_meta(getattr(template_entry, "meta", None)),
            _clean_meta(handler_opt.get(opt_keys.for_field("meta", kind))),
        ):
            if candidate is not None:
                merged_meta.update(candidate)
        return title, icons, (merged_meta or None)

    def _resolved_ui_mime_types(self) -> "list[str]":
        """Resolve advertised SEP-1865 MCP Apps UI MIME types in deterministic order."""
        if self.config.ui_mime_types is not None:
            return list(dict.fromkeys(mime for mime in self.config.ui_mime_types if mime))

        candidates: list[tuple[str, BaseRouteHandler, ResourceTemplate | None]] = [
            (_resource_uri(name, handler, self.config), handler, None)
            for name, handler in self.discovered_resources.items()
            if should_include_handler(name, _handler_tags(handler), self.config)
        ]
        if self.registry is not None:
            candidates.extend(
                (entry.template, entry.handler, entry)
                for entry in self.registry.templates.values()
                if should_include_handler(entry.name, _handler_tags(entry.handler), self.config)
            )

        advertised: list[str] = []
        for uri, handler, template_entry in candidates:
            explicit_mime = _explicit_resource_mime_type(handler, self.config)
            handler_mt = _handler_media_type(handler)
            resolved_mime = _resource_mime_type(handler, self.config)
            _, _, meta = self._resolve_descriptor_fields(handler, kind="resource", template_entry=template_entry)
            is_ui_resource = (
                uri.lower().startswith("ui://")
                or "profile=mcp-app" in resolved_mime.lower()
                or (meta is not None and "ui" in meta)
            )
            if not is_ui_resource:
                continue
            if explicit_mime is not None:
                advertised.append(explicit_mime)
            elif handler_mt is not None and handler_mt not in {"application/json", "text/plain"}:
                advertised.append(handler_mt)
            else:
                advertised.append(UI_MIME_TYPE)
        return list(dict.fromkeys(advertised))

    def _should_include_skill(self, skill: "Skill") -> "bool":
        return should_include_handler(skill.name, set(skill.tags), self.config)

    async def _get_skill_prompt(
        self,
        prompt_name: "str",
        params: "dict[str, Any]",
        context: "MCPRequestContext",
        catalog: "SkillCatalog",
    ) -> "dict[str, Any]":
        if not isinstance(prompt_name, str) or not prompt_name:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'"))
        skill_uri = f"{SKILL_URI_PREFIX}{prompt_name}/SKILL.md"
        token = _request_context.set(context)
        try:
            try:
                match = await catalog.get_file(context.request, skill_uri)
            except LookupError as exc:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Prompt not found: {prompt_name}")
                ) from exc
            if match is None or not self._should_include_skill(match.skill):
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Prompt not found: {prompt_name}")
                )
            prompt_args = params.get("arguments", {})
            if not isinstance(prompt_args, dict):
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message="Prompt arguments must be an object")
                )
            for arg_key, arg_val in prompt_args.items():
                if not isinstance(arg_val, str):
                    raise JSONRPCErrorException(
                        JSONRPCError(
                            code=INVALID_PARAMS,
                            message=f"Argument '{arg_key}' must be a string, got {type(arg_val).__name__}",
                        )
                    )
            if prompt_args:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=f"Unknown prompt argument(s): {', '.join(sorted(prompt_args))}",
                    )
                )
            try:
                body = await catalog.read_file(context.request, match)
                text = body.decode("utf-8")
            except LookupError as exc:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Prompt not found: {prompt_name}")
                ) from exc
            except SkillIntegrityError as exc:
                raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message=str(exc))) from exc
            except UnicodeDecodeError as exc:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Skill prompt '{prompt_name}' is not valid UTF-8")
                ) from exc
            except OSError as exc:
                _logger.exception("Skill prompt read failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Failed to read skill prompt '{prompt_name}'")
                ) from exc
        finally:
            _request_context.reset(token)

        result: dict[str, Any] = {
            "description": match.skill.description,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
        if match.skill.meta:
            result["_meta"] = dict(match.skill.meta)
        return result


def _require_tasks_capability(context: "MCPRequestContext") -> None:
    extensions = (context.client_capabilities or {}).get("extensions")
    if not isinstance(extensions, dict) or TASKS_EXTENSION not in extensions:
        raise JSONRPCErrorException(
            JSONRPCError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message=f"The {TASKS_EXTENSION} client capability is required",
                data={"requiredCapabilities": {"extensions": {TASKS_EXTENSION: {}}}},
            )
        )


def _validate_subscription_uri(uri: "Any") -> "str":
    if not isinstance(uri, str) or not uri or any(ch.isspace() for ch in uri):
        raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Invalid or missing param: 'uri'"))
    scheme, sep, remainder = uri.partition(":")
    if not sep or not scheme or not remainder:
        raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Invalid or missing param: 'uri'"))
    return uri
