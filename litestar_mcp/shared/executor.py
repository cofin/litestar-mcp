"""Execute route handlers through Litestar's full HTTP request-pipeline."""

import inspect
import logging
import re
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

from litestar import Litestar, Request
from litestar._asgi.routing_trie.traversal import parse_path_params
from litestar.exceptions import ImproperlyConfiguredException, SerializationException
from litestar.response import Response
from litestar.serialization import decode_json, encode_json
from litestar.types.empty import Empty
from litestar.utils.sync import ensure_async_callable

from litestar_mcp.content import MCPBlobResource, MCPInputRequiredResult, MCPResourceLink, MCPToolResult
from litestar_mcp.utils.handler_signature import get_advertised_handler_parameters

if TYPE_CHECKING:
    from litestar.handlers.base import BaseRouteHandler
    from litestar.handlers.http_handlers.base import HTTPRouteHandler
    from litestar.types import Message, Receive, Scope, Send
    from litestar.types.internal_types import PathParameterDefinition

_logger = logging.getLogger(__name__)

_NON_JSON_STATUS = 500
_ERROR_STATUS_FLOOR = 400
_INTERNAL_DISPATCH_SCOPE_KEY = "litestar_mcp.internal_dispatch"
_TEXT_MEDIA_TYPES = {
    "application/javascript",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
}


@dataclass(frozen=True, slots=True)
class CapturedHandlerResponse:
    """Captured response from a Litestar handler dispatch."""

    content: Any
    status_code: int
    body: bytes
    media_type: str


class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a handler cannot be dispatched in stdio / CLI mode."""

    def __init__(self, handler_name: str, reason: str) -> None:
        super().__init__(f"Handler '{handler_name}' cannot be called from CLI: {reason}")


class PathParamCoercionError(ValueError):
    """Raised when a typed path parameter cannot be coerced from its raw value."""

    def __init__(self, name: str, raw: Any, cause: Exception) -> None:
        super().__init__(f"Invalid value for path parameter {name!r}: {raw!r} ({cause})")
        self.name = name
        self.raw = raw


_PATH_PARAMETERS_CACHE: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()


def find_route_path_parameters(app: Litestar, handler: "BaseRouteHandler") -> dict[str, Any]:
    """Look up path_parameters for the route owning handler."""
    cached = _PATH_PARAMETERS_CACHE.get(handler)
    if cached is not None:
        return dict(cached)
    for route in app.routes:
        for candidate in getattr(route, "route_handlers", []):
            if candidate is handler:
                found = dict(getattr(route, "path_parameters", {}))
                _PATH_PARAMETERS_CACHE[handler] = found
                return dict(found)
        candidate = getattr(route, "route_handler", None)
        if candidate is handler:
            found = dict(getattr(route, "path_parameters", {}))
            _PATH_PARAMETERS_CACHE[handler] = found
            return dict(found)
    return {}


def _parser_would_reject(defn: "PathParameterDefinition", raw: Any) -> bool:
    if defn.parser is None:
        return False
    try:
        defn.parser(str(raw))
    except (ValueError, TypeError):
        return True
    return False


def _coerce_path_params(
    path_parameters: "dict[str, PathParameterDefinition]",
    raw_values: dict[str, Any],
) -> dict[str, Any]:
    if not path_parameters or not raw_values:
        return dict(raw_values)
    present = tuple(defn for name, defn in path_parameters.items() if name in raw_values)
    if not present:
        return {}
    try:
        values_tuple = tuple(str(raw_values[defn.name]) for defn in present)
        return parse_path_params(present, values_tuple)
    except (ValueError, TypeError) as exc:
        offending = next(
            (defn.name for defn in present if _parser_would_reject(defn, raw_values[defn.name])),
            present[0].name,
        )
        raise PathParamCoercionError(offending, raw_values[offending], exc) from exc


def _substitute_path(template: str, path_params: dict[str, Any]) -> str:
    result = template
    for key, value in path_params.items():
        pattern = re.compile(r"\{" + re.escape(key) + r"(?::[^}]*)?\}")
        result = pattern.sub(str(value), result)
    return result


def _split_handler_args(
    handler: "BaseRouteHandler",
    args: dict[str, Any],
    path_parameters: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    advertised_params = get_advertised_handler_parameters(handler, path_parameters=path_parameters)
    wire_scalar_keys = {p.wire_name for p in advertised_params if p.python_name != "data"}
    has_data = "data" in handler.parsed_fn_signature.parameters

    path_values = {k: args[k] for k in path_parameters if k in args}
    remaining = {k: v for k, v in args.items() if k not in path_values}
    query_payload = {k: v for k, v in remaining.items() if k in wire_scalar_keys}

    body_payload: Any = {}
    if has_data:
        if "data" in remaining:
            body_payload = remaining["data"]
        else:
            body_payload = {k: v for k, v in remaining.items() if k not in query_payload}

    body = encode_json(body_payload, serializer=handler.default_serializer) if body_payload else b""
    return path_values, query_payload, body


def _blank_http_scope(app: Litestar) -> dict[str, Any]:
    return {
        "type": "http",
        "app": app,
        "litestar_app": app,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "scheme": "http",
        "root_path": "",
        "server": ("synthetic-dispatcher", 0),
        "client": ("synthetic-dispatcher", 0),
        "state": {},
        "session": {},
        "user": None,
        "auth": None,
        "extensions": {},
    }


def build_dispatch_scope(
    handler: "BaseRouteHandler",
    args: dict[str, Any],
    *,
    base_scope: dict[str, Any] | None,
    scope_overrides: dict[str, Any] | None,
    app: Litestar,
    path_parameters: dict[str, Any],
) -> tuple[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]]]:
    """Shape handler args into an ASGI scope + receive."""
    path_values, query_values, body = _split_handler_args(handler, args, path_parameters)
    coerced_path_values = _coerce_path_params(path_parameters, path_values)
    path_template = next(iter(handler.paths)) if handler.paths else "/"
    path = _substitute_path(path_template, coerced_path_values)
    query_string = urlencode(query_values, doseq=True).encode("ascii") if query_values else b""

    scope = _blank_http_scope(app)
    if base_scope is not None:
        inherited_state = dict(base_scope.get("state", {}))
        inherited_state.pop("_ls_connection_state", None)
        scope["state"] = inherited_state
        for passthrough in ("user", "auth", "session"):
            if passthrough in base_scope:
                scope[passthrough] = base_scope[passthrough]
    elif scope_overrides is not None:
        if "state" in scope_overrides:
            scope["state"] = dict(scope_overrides.get("state") or {})
        if "session" in scope_overrides:
            scope["session"] = dict(scope_overrides.get("session") or {})
        for passthrough in ("user", "auth"):
            if passthrough in scope_overrides:
                value = scope_overrides[passthrough]
                scope[passthrough] = dict(value) if isinstance(value, Mapping) else value

    http_methods = getattr(handler, "http_methods", None) or ("POST",)
    method = next(iter(http_methods))
    headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")] if body else []

    scope.update(
        {
            "method": method,
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string,
            "headers": headers,
            "path_params": coerced_path_values,
            "route_handler": handler,
            "path_template": path_template,
            _INTERNAL_DISPATCH_SCOPE_KEY: True,
        },
    )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return scope, receive


async def open_stdio_dishka_container(app: Litestar, scope: dict[str, Any], stack: AsyncExitStack) -> None:
    """Open a request-scoped child Dishka container for stdio dispatch."""
    container_factory = getattr(app.state, "dishka_container", None)
    if container_factory is None:
        return
    child = await stack.enter_async_context(container_factory())
    scope.setdefault("state", {})["dishka_container"] = child


def _is_json_media_type(media_type: str) -> bool:
    return media_type == "application/json" or media_type.endswith("+json")


def _is_text_media_type(media_type: str) -> bool:
    return media_type.startswith("text/") or media_type in _TEXT_MEDIA_TYPES or media_type.endswith("+xml")


async def _capture_asgi_response(
    asgi_app: Any,
    request: Request[Any, Any, Any],
) -> CapturedHandlerResponse:
    status_code = 0
    media_type = ""
    body_chunks: list[bytes] = []

    async def _sink_send(message: "Message") -> None:
        nonlocal status_code, media_type
        msg_type = message.get("type")
        if msg_type == "http.response.start":
            status_code = cast("int", message.get("status", 0))
            headers = cast("Sequence[tuple[bytes, bytes]]", message.get("headers", ()) or ())
            for key, value in headers:
                if key.lower() == b"content-type":
                    media_type = value.decode("latin-1").split(";")[0].strip()
                    break
        elif msg_type == "http.response.body":
            body = cast("bytes", message.get("body", b"") or b"")
            body_chunks.append(bytes(body))

    scope = cast("Scope", request.scope)
    receive: Receive = request.receive
    send: Send = request.app._wrap_send(_sink_send, scope)  # noqa: SLF001
    await asgi_app(scope, receive, send)

    if status_code == 0:
        return CapturedHandlerResponse(
            content={"error": "Handler exited without sending an ASGI response"},
            status_code=_NON_JSON_STATUS,
            body=b"",
            media_type=media_type,
        )

    body = b"".join(body_chunks)
    if not body:
        return CapturedHandlerResponse(content=None, status_code=status_code, body=b"", media_type=media_type)

    if _is_json_media_type(media_type):
        try:
            content = decode_json(body)
        except SerializationException:
            return CapturedHandlerResponse(
                content={"error": "invalid JSON response from handler", "media_type": media_type or "unknown"},
                status_code=_NON_JSON_STATUS,
                body=body,
                media_type=media_type,
            )
        return CapturedHandlerResponse(content=content, status_code=status_code, body=body, media_type=media_type)

    if _is_text_media_type(media_type):
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError:
            content = body
        return CapturedHandlerResponse(content=content, status_code=status_code, body=body, media_type=media_type)

    return CapturedHandlerResponse(content=body, status_code=status_code, body=body, media_type=media_type)


async def _enforce_guards(handler: "BaseRouteHandler", request: Request[Any, Any, Any]) -> None:
    for guard in handler.resolve_guards():
        result = guard(request, handler)
        if inspect.isawaitable(result):
            await result


def _hook_is_app_level(hook: Any, app: Litestar, attr: str) -> bool:
    app_hook = getattr(app, attr, None)
    return app_hook is not None and hook is app_hook


async def _run_before_request(
    handler: "BaseRouteHandler",
    request: Request[Any, Any, Any],
) -> Any:
    http_handler = cast("HTTPRouteHandler", handler)
    hook = http_handler.resolve_before_request()
    if hook is None or _hook_is_app_level(hook, request.app, "before_request"):
        return Empty
    raw: Any = hook(request)
    if inspect.isawaitable(raw):
        raw = await raw
    return raw


async def _run_after_response(
    handler: "BaseRouteHandler",
    request: Request[Any, Any, Any],
) -> None:
    http_handler = cast("HTTPRouteHandler", handler)
    hook = http_handler.resolve_after_response()
    if hook is None or _hook_is_app_level(hook, request.app, "after_response"):
        return
    try:
        result = hook(request)
        if inspect.isawaitable(result):
            await result
    except Exception:
        _logger.exception("after_response hook failed during handler dispatch")


async def _run_after_exception_hooks(
    app: Litestar,
    request: Request[Any, Any, Any],
    exc: Exception,
) -> None:
    observers = getattr(app, "after_exception", None) or []
    for observer in observers:
        try:
            result = observer(exc, request.scope)
            if inspect.isawaitable(result):
                await result
        except Exception:
            _logger.exception("after_exception hook failed during handler dispatch")


async def _dispatch_via_exception_handlers(
    handler: "BaseRouteHandler",
    request: Request[Any, Any, Any],
    exc: Exception,
) -> CapturedHandlerResponse | None:
    exception_handlers = handler.resolve_exception_handlers() or {}
    matched = None
    for exc_type in type(exc).__mro__:
        candidate = exception_handlers.get(exc_type)
        if candidate is not None:
            matched = candidate
            break
    if matched is None:
        return None

    raw: Any = matched(request, exc)
    if inspect.isawaitable(raw):
        raw = await raw

    if isinstance(raw, Response):
        status = int(getattr(raw, "status_code", 200))
        body = raw.content if isinstance(raw.content, bytes) else encode_json(raw.content)
        media_type = str(getattr(raw, "media_type", "") or "application/json")
        return CapturedHandlerResponse(content=raw.content, status_code=status, body=body, media_type=media_type)

    return CapturedHandlerResponse(content=raw, status_code=500, body=encode_json(raw), media_type="application/json")


async def run_handler_pipeline(
    handler: "BaseRouteHandler",
    app: Litestar,
    path_parameters: dict[str, Any],
    dispatch_request: Request[Any, Any, Any],
    stack: AsyncExitStack,
) -> CapturedHandlerResponse:
    """Run guards, hooks, dependency resolution, handler dispatch, and response rendering."""
    try:
        try:
            await _enforce_guards(handler, dispatch_request)

            short_circuit = await _run_before_request(handler, dispatch_request)
            if short_circuit is not Empty and short_circuit:
                raw_result = short_circuit
            else:
                kwargs_model = handler.create_kwargs_model(path_parameters=path_parameters)
                kwargs = await kwargs_model.to_kwargs(connection=dispatch_request)
                cleanup_group = await kwargs_model.resolve_dependencies(dispatch_request, kwargs)
                await stack.enter_async_context(cleanup_group)
                parsed_kwargs = handler.signature_model.parse_values_from_connection_kwargs(
                    connection=dispatch_request,
                    kwargs=kwargs,
                )
                handler_fn = ensure_async_callable(handler.fn)
                raw_result = await handler_fn(**parsed_kwargs)

            if isinstance(raw_result, (MCPBlobResource, MCPInputRequiredResult, MCPResourceLink, MCPToolResult)):
                return CapturedHandlerResponse(
                    content=raw_result,
                    status_code=200,
                    body=b"",
                    media_type="application/json",
                )

            http_handler = cast("HTTPRouteHandler", handler)
            asgi_app = await http_handler.to_response(app=app, data=raw_result, request=dispatch_request)
            response = await _capture_asgi_response(asgi_app, dispatch_request)
        except Exception as exc:
            await _run_after_exception_hooks(app, dispatch_request, exc)
            handled = await _dispatch_via_exception_handlers(handler, dispatch_request, exc)
            if handled is None:
                raise
            response = handled
    finally:
        await _run_after_response(handler, dispatch_request)
    return response
