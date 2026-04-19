"""Execute MCP tools through Litestar's full HTTP request-pipeline.

Each ``tools/call`` synthesizes a Litestar :class:`Request` shaped like a real
HTTP request for the handler's declared route, then runs the framework's own
dispatch pipeline against it. Path params, query kwargs, ``data: StructT``
bodies, ``FromDishka[T]``, ``request: Request``, guards, dependencies,
``before_request`` / ``after_request`` / ``after_response``,
``after_exception`` observers, and ownership-layer ``exception_handlers`` all
work the way they would for a normal HTTP handler — no MCP-specific plumbing.

Response rendering delegates to :meth:`HTTPRouteHandler.to_response` + a
capture-send driver, so every Litestar feature baked into the ``to_response``
closure (``type_encoders``, ``response_class``, ``media_type``, cookies,
headers, ``after_request``) is picked up automatically. The JSON-RPC envelope
carries the decoded body and maps ``status_code >= 400`` to
``isError: true``; HTTP-only semantics (headers, cookies) are a documented
transport caveat.
"""

from __future__ import annotations

import inspect
import logging
import re
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

import msgspec
from litestar import Litestar, Request
from litestar.exceptions import ImproperlyConfiguredException
from litestar.response import Response
from litestar.types.empty import Empty
from litestar.utils.sync import ensure_async_callable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from litestar.handlers.base import BaseRouteHandler
    from litestar.handlers.http_handlers.base import HTTPRouteHandler

__all__ = ("MCPToolErrorResult", "NotCallableInCLIContextError", "execute_tool")

_logger = logging.getLogger(__name__)

_NON_JSON_STATUS = 500
_ERROR_STATUS_FLOOR = 400


class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a tool cannot be dispatched in stdio / CLI mode."""

    def __init__(self, handler_name: str, reason: str) -> None:
        """Initialize the exception.

        Args:
            handler_name: Name of the handler that cannot be called.
            reason: Human-readable explanation.
        """
        super().__init__(f"Tool '{handler_name}' cannot be called from the CLI: {reason}")


class MCPToolErrorResult(Exception):  # noqa: N818
    """Carries a ``4xx``/``5xx`` tool-call payload from executor to route layer.

    Raised when ``handler.to_response`` produces a ``status_code >= 400`` or
    when an ``exception_handlers`` dispatch resolves to an error response.
    The JSON-RPC route layer catches this and maps it to ``isError: true`` in
    the ``tools/call`` result.
    """

    def __init__(self, content: Any) -> None:
        """Initialize with the already-rendered JSON-RPC content payload.

        Args:
            content: Decoded response body (typically a ``dict``).
        """
        super().__init__("MCP tool returned error response")
        self.content = content


async def _enforce_guards(handler: BaseRouteHandler, request: Request[Any, Any, Any]) -> None:
    """Run each guard resolved from ``handler.ownership_layers`` against ``request``.

    Walks app → router → controller → handler guards, same as Litestar's own
    HTTP dispatch. First failure aborts the invocation.
    """
    for guard in handler.resolve_guards():
        result = guard(request, handler)
        if inspect.isawaitable(result):
            await result


def _hook_is_app_level(hook: Any, app: Litestar, attr: str) -> bool:
    """True when ``hook`` is the app-level hook already fired on the outer ``/mcp`` request.

    Litestar runs ``before_request`` / ``after_response`` as part of the
    handler's response pipeline, not via middleware — so the hook fires
    exactly once per HTTP request. Since each ``tools/call`` IS an HTTP
    request to ``/mcp``, the app-level hook already runs there. Firing it
    again for the synthesized tool dispatch would double-invoke. Hooks
    declared at route / controller / router scope live BELOW the ``/mcp``
    endpoint and don't fire on the outer request, so they must run here.
    """
    app_hook = getattr(app, attr, None)
    return app_hook is not None and hook is app_hook


async def _run_before_request(
    handler: BaseRouteHandler,
    request: Request[Any, Any, Any],
) -> Any:
    """Invoke the closest-wins ``before_request`` hook.

    Returns:
        :data:`Empty` when no hook is declared or the resolved hook is the
        app-level hook (which already fired on the outer ``/mcp`` request);
        otherwise the hook's return value (truthy short-circuits the
        handler, falsy falls through to normal dispatch).
    """
    http_handler = cast("HTTPRouteHandler", handler)
    hook = http_handler.resolve_before_request()
    if hook is None:
        return Empty
    if _hook_is_app_level(hook, request.app, "before_request"):
        return Empty
    raw: Any = hook(request)
    if inspect.isawaitable(raw):
        raw = await raw
    return raw


async def _run_after_response(
    handler: BaseRouteHandler,
    request: Request[Any, Any, Any],
) -> None:
    """Fire the closest-wins ``after_response`` hook; log and swallow failures.

    Skipped when the resolved hook is the app-level hook — the outer
    ``/mcp`` HTTP request already fires it.
    """
    http_handler = cast("HTTPRouteHandler", handler)
    hook = http_handler.resolve_after_response()
    if hook is None:
        return
    if _hook_is_app_level(hook, request.app, "after_response"):
        return
    try:
        result = hook(request)
        if inspect.isawaitable(result):
            await result
    except Exception:
        _logger.exception("after_response hook failed during MCP tool dispatch")


async def _run_after_exception_hooks(
    app: Litestar,
    request: Request[Any, Any, Any],
    exc: Exception,
) -> None:
    """Fire app-level ``after_exception`` observers; log and swallow each failure.

    Parity with Litestar HTTP: observers fire BEFORE ``exception_handlers``
    dispatch, so recovery still happens even when an observer throws.
    """
    observers = getattr(app, "after_exception", None) or []
    for observer in observers:
        try:
            result = observer(exc, request.scope)
            if inspect.isawaitable(result):
                await result
        except Exception:
            _logger.exception("after_exception hook failed during MCP tool dispatch")


async def _capture_asgi_response(
    asgi_app: Any,
    request: Request[Any, Any, Any],
) -> tuple[Any, int]:
    """Drive an ASGIApp against a sink-send, returning (content, status_code).

    ``handler.to_response`` returns an :class:`ASGIResponse`. We invoke it
    with the dispatch request's scope/receive and a send callable that
    captures the ``http.response.start`` / ``http.response.body`` messages.
    The body is decoded as JSON; if the handler returned a non-JSON media
    type we surface a generic ``{"error": ..., "media_type": ...}`` payload
    with ``status_code=500`` so JSON-RPC callers see a well-formed error.
    """
    status_code = 0
    media_type = ""
    body_chunks: list[bytes] = []

    async def _sink_send(message: dict[str, Any]) -> None:
        nonlocal status_code, media_type
        msg_type = message.get("type")
        if msg_type == "http.response.start":
            status_code = int(message.get("status", 0))
            for key, value in message.get("headers", []) or []:
                if key.lower() == b"content-type":
                    media_type = value.decode("latin-1").split(";")[0].strip()
                    break
        elif msg_type == "http.response.body":
            body_chunks.append(bytes(message.get("body", b"") or b""))

    await asgi_app(cast("Any", request.scope), cast("Any", request.receive), _sink_send)

    body = b"".join(body_chunks)
    if not body:
        return None, status_code
    try:
        content = msgspec.json.decode(body)
    except msgspec.DecodeError:
        return (
            {"error": "non-JSON response from MCP handler", "media_type": media_type or "unknown"},
            _NON_JSON_STATUS,
        )
    return content, status_code


async def _dispatch_via_exception_handlers(
    handler: BaseRouteHandler,
    request: Request[Any, Any, Any],
    exc: Exception,
) -> tuple[Any, bool] | None:
    """Walk ``handler.resolve_exception_handlers()`` MRO-style for ``exc``.

    Returns:
        ``(content, is_error)`` when a handler matches and renders a
        response; ``None`` when no handler matches (caller must re-raise).
    """
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
        is_error = status >= _ERROR_STATUS_FLOOR
        return raw.content, is_error

    # Exception handler returned raw data — treat as an error render since
    # it was triggered by a raised exception.
    return raw, True


def _find_route_path_parameters(app: Litestar, handler: BaseRouteHandler) -> dict[str, Any]:
    """Look up ``path_parameters`` for the route owning ``handler``.

    The MCP registry stores handlers but not their owning route, so we walk
    ``app.routes`` on each dispatch. Path-parameter metadata is small and
    ``app.routes`` is modest in size; caching hasn't shown up as a hot spot.
    """
    for route in app.routes:
        for candidate in getattr(route, "route_handlers", []):
            if candidate is handler:
                return dict(getattr(route, "path_parameters", {}))
        candidate = getattr(route, "route_handler", None)
        if candidate is handler:
            return dict(getattr(route, "path_parameters", {}))
    return {}


def _substitute_path(template: str, path_params: dict[str, Any]) -> str:
    """Replace ``{name}`` / ``{name:type}`` placeholders in ``template``.

    Matches the exact parameter name (with optional ``:type`` suffix) so a
    param named ``id`` doesn't accidentally rewrite ``{identifier}``.
    """
    result = template
    for key, value in path_params.items():
        pattern = re.compile(r"\{" + re.escape(key) + r"(?::[^}]*)?\}")
        result = pattern.sub(str(value), result)
    return result


def _split_tool_args(
    handler: BaseRouteHandler,
    tool_args: dict[str, Any],
    path_parameters: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Partition ``tool_args`` into (path_params, query_params, body_bytes).

    Precedence for each key:

    1. Path parameter — if the name appears in the route's path template.
    2. Scalar handler kwarg — if the name matches a non-``data`` signature
       parameter, it's bound as a query parameter so the native extractor
       parses it via the signature model.
    3. Body — if the handler declares a ``data`` parameter, leftover keys
       become members of the JSON body that Litestar decodes into the
       ``data`` struct.
    4. Dropped — if none of the above match. (A later framework-level
       error surfaces on handlers whose signatures genuinely can't accept
       the key.)
    """
    sig_params = handler.parsed_fn_signature.parameters
    has_data = "data" in sig_params
    scalar_sig_names = {name for name in sig_params if name != "data"}

    path_values = {k: tool_args[k] for k in path_parameters if k in tool_args}
    remaining = {k: v for k, v in tool_args.items() if k not in path_values}

    query_payload = {k: v for k, v in remaining.items() if k in scalar_sig_names}

    body_payload: Any = {}
    if has_data:
        if "data" in remaining:
            body_payload = remaining["data"]
        else:
            body_payload = {k: v for k, v in remaining.items() if k not in query_payload}

    body = msgspec.json.encode(body_payload) if body_payload else b""
    return path_values, query_payload, body


def _blank_http_scope(app: Litestar) -> dict[str, Any]:
    """Return a minimum ASGI HTTP scope for stdio-mode dispatch.

    Keys mirror what ``litestar.testing.RequestFactory`` populates so
    Litestar's native dispatch pipeline finds everything it expects.
    """
    return {
        "type": "http",
        "app": app,
        "litestar_app": app,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "scheme": "http",
        "root_path": "",
        "server": ("mcp-stdio", 0),
        "client": ("mcp-stdio", 0),
        "state": {},
        "session": {},
        "user": None,
        "auth": None,
        "extensions": {},
    }


def _build_dispatch_scope(
    handler: BaseRouteHandler,
    tool_args: dict[str, Any],
    *,
    base_scope: dict[str, Any] | None,
    app: Litestar,
    path_parameters: dict[str, Any],
) -> tuple[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]]]:
    """Shape ``tool_args`` into an ASGI scope + receive for ``handler``.

    HTTP mode (``base_scope`` from the inbound /mcp request) inherits
    middleware-populated state (``scope["state"]`` — e.g. Dishka's request
    container, Ch3's auth-middleware user) so request-scoped DI flows through.
    Stdio mode starts from a blank scope.
    """
    path_values, query_values, body = _split_tool_args(handler, tool_args, path_parameters)
    path_template = next(iter(handler.paths)) if handler.paths else "/"
    path = _substitute_path(path_template, path_values)
    query_string = urlencode(query_values, doseq=True).encode("ascii") if query_values else b""

    scope = _blank_http_scope(app)
    if base_scope is not None:
        # Cherry-pick middleware-populated keys from the inbound request so
        # auth / Dishka / session context flows through, but skip cached body
        # / header / path state that would confuse the dispatch pipeline.
        inherited_state = dict(base_scope.get("state", {}))
        inherited_state.pop("_ls_connection_state", None)
        scope["state"] = inherited_state
        for passthrough in ("user", "auth", "session"):
            if passthrough in base_scope:
                scope[passthrough] = base_scope[passthrough]

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
            "path_params": path_values,
            "route_handler": handler,
            "path_template": path_template,
        },
    )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return scope, receive


async def _open_stdio_dishka_container(app: Litestar, scope: dict[str, Any], stack: AsyncExitStack) -> None:
    """Open a request-scoped child Dishka container for stdio dispatch.

    The ``setup_dishka`` integration stores a root container factory on
    ``app.state.dishka_container``. HTTP mode has a middleware that opens a
    child per request; stdio needs to open one manually so ``FromDishka[T]``
    resolves identically in both modes.
    """
    container_factory = getattr(app.state, "dishka_container", None)
    if container_factory is None:
        return
    child = await stack.enter_async_context(container_factory())
    scope.setdefault("state", {})["dishka_container"] = child


async def execute_tool(
    handler: BaseRouteHandler,
    app: Litestar,
    tool_args: dict[str, Any],
    *,
    request: Request[Any, Any, Any] | None = None,
) -> Any:
    """Execute an MCP tool handler through Litestar's full HTTP pipeline.

    Runs, in order:

    1. Guards from every ownership layer (app → router → controller → handler).
    2. ``before_request`` (closest-wins). A truthy return short-circuits the
       handler; a falsy return (``None`` / ``""`` / ``0`` / ``[]`` / ``{}``)
       falls through to normal dispatch.
    3. ``create_kwargs_model`` → ``to_kwargs`` → ``resolve_dependencies`` →
       ``parse_values_from_connection_kwargs`` → ``handler.fn(**parsed)``
       (skipped on short-circuit).
    4. ``handler.to_response(...)`` + capture-send — this is where
       ``after_request``, ``type_encoders``, response-class selection, and
       media-type negotiation happen.
    5. On any raised exception: app-level ``after_exception`` observers fire
       (observer failures are logged and swallowed), then
       ``handler.resolve_exception_handlers()`` is walked MRO-style. A
       matching handler's returned :class:`Response` renders in-place; if
       no handler matches, the exception propagates to the route layer's
       JSON-RPC blanket catch.
    6. ``after_response`` (closest-wins) fires in ``finally``, exactly once,
       on every path — success, short-circuit, guard failure, exception.

    Transport caveat: JSON-RPC has no HTTP semantics, so response headers and
    cookies set in ``after_request`` or by the response class are silently
    dropped. ``status_code >= 400`` maps to ``isError: true``.

    Args:
        handler: The MCP-tool-marked route handler.
        app: The running Litestar application.
        tool_args: Arguments from the MCP ``tools/call`` request; routed
            into path / query / body based on the handler's signature.
        request: Inbound :class:`~litestar.Request` in HTTP mode, ``None``
            for CLI / stdio invocations.

    Returns:
        The decoded response body for success paths.

    Raises:
        MCPToolErrorResult: When the handler pipeline or an exception handler
            renders ``status_code >= 400``. The ``content`` attribute carries
            the already-rendered payload for the JSON-RPC route layer.
    """
    path_parameters = _find_route_path_parameters(app, handler)

    async with AsyncExitStack() as stack:
        base_scope: dict[str, Any] | None = cast("dict[str, Any]", request.scope) if request is not None else None
        dispatch_scope, receive = _build_dispatch_scope(
            handler,
            tool_args,
            base_scope=base_scope,
            app=app,
            path_parameters=path_parameters,
        )
        if request is None:
            await _open_stdio_dishka_container(app, dispatch_scope, stack)

        dispatch_request: Request[Any, Any, Any] = Request(
            cast("Any", dispatch_scope),
            receive=cast("Any", receive),
        )

        content: Any = None
        is_error = False
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

                http_handler = cast("HTTPRouteHandler", handler)
                asgi_app = await http_handler.to_response(app=app, data=raw_result, request=dispatch_request)
                content, status = await _capture_asgi_response(asgi_app, dispatch_request)
                is_error = status >= _ERROR_STATUS_FLOOR
            except Exception as exc:
                await _run_after_exception_hooks(app, dispatch_request, exc)
                handled = await _dispatch_via_exception_handlers(handler, dispatch_request, exc)
                if handled is None:
                    raise
                content, is_error = handled
        finally:
            await _run_after_response(handler, dispatch_request)

    if is_error:
        raise MCPToolErrorResult(content)
    return content
