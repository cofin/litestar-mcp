"""Execute MCP tools through Litestar's native request-pipeline.

Each ``tools/call`` synthesizes a Litestar ``Request`` shaped like a real HTTP
request for the handler's declared route, then runs the framework's own
``KwargsModel`` / ``SignatureModel`` / dependency-resolution pipeline against
it. Path params, query kwargs, ``data: StructT`` bodies, ``FromDishka[T]``,
``request: Request``, guards, and ``Provide(...)`` dependencies all work the
way they would for a normal HTTP handler — no MCP-specific DI plumbing.
"""

from __future__ import annotations

import inspect
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

import msgspec
from litestar import Litestar, Request
from litestar.exceptions import ImproperlyConfiguredException
from litestar.utils.sync import ensure_async_callable

from litestar_mcp.typing import schema_dump

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from litestar.handlers.base import BaseRouteHandler

__all__ = ("NotCallableInCLIContextError", "ToolExecutionContext", "execute_tool")


@dataclass
class ToolExecutionContext:
    """Observability snapshot of the dispatched tool invocation.

    Handlers receive user / claims / DI through the synthesized ``request``
    exactly as they would for an HTTP request.

    Attributes:
        app: The running Litestar application.
        handler: The MCP-tool-marked route handler being invoked.
        tool_args: Arguments from the MCP ``tools/call`` request.
        request: The live (HTTP mode) or synthesized (stdio mode) Request the
            handler dispatches against.
    """

    app: Litestar
    handler: BaseRouteHandler
    tool_args: dict[str, Any]
    request: Request[Any, Any, Any]


class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a tool cannot be dispatched in stdio / CLI mode."""

    def __init__(self, handler_name: str, reason: str) -> None:
        """Initialize the exception.

        Args:
            handler_name: Name of the handler that cannot be called.
            reason: Human-readable explanation.
        """
        super().__init__(f"Tool '{handler_name}' cannot be called from the CLI: {reason}")


async def _enforce_guards(handler: BaseRouteHandler, request: Request[Any, Any, Any]) -> None:
    """Run each guard resolved from ``handler.ownership_layers`` against ``request``.

    Walks app → router → controller → handler guards, same as Litestar's own
    HTTP dispatch. First failure aborts the invocation.
    """
    for guard in handler.resolve_guards():
        result = guard(request, handler)
        if inspect.isawaitable(result):
            await result


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
    body_payload = {k: v for k, v in remaining.items() if k not in query_payload} if has_data else {}

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
    """Execute an MCP tool handler through Litestar's native dispatch pipeline.

    In HTTP mode (``request`` provided), the executor synthesizes a sibling
    ``Request`` shaped like a real HTTP request for ``handler``'s declared
    route, inheriting middleware-populated state from the inbound request. In
    stdio mode (``request=None``), it builds a fresh scope and opens a child
    Dishka container when the app has one. Both modes run the same dispatch
    pipeline: ``create_kwargs_model`` → ``to_kwargs`` → ``resolve_dependencies``
    → ``parse_values_from_connection_kwargs`` → ``handler.fn(**parsed)``.

    Guards from every ownership layer (app → router → controller → handler)
    run against the dispatch request before any dependency resolution.

    Args:
        handler: The MCP-tool-marked route handler.
        app: The running Litestar application.
        tool_args: Arguments from the MCP ``tools/call`` request; routed
            into path / query / body based on the handler's signature.
        request: Inbound :class:`~litestar.Request` in HTTP mode, ``None``
            for CLI / stdio invocations.

    Returns:
        The handler's return value, ``schema_dump``-ed when not a primitive.
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

        await _enforce_guards(handler, dispatch_request)

        kwargs_model = handler.create_kwargs_model(path_parameters=path_parameters)
        kwargs = await kwargs_model.to_kwargs(connection=dispatch_request)
        cleanup_group = await kwargs_model.resolve_dependencies(dispatch_request, kwargs)
        await stack.enter_async_context(cleanup_group)
        parsed_kwargs = handler.signature_model.parse_values_from_connection_kwargs(
            connection=dispatch_request,
            kwargs=kwargs,
        )

        handler_fn = ensure_async_callable(handler.fn)
        result = await handler_fn(**parsed_kwargs)

    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        return schema_dump(result)
    return result
