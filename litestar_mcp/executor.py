"""Core execution logic for invoking MCP tools.

Dependency resolution is delegated to Litestar's own ``KwargsModel`` machinery
so that the filtering (only handler-consumed deps), transitive walking, and
topological ordering are all handled by the framework we're piggybacking on.
We intentionally reach into ``litestar._kwargs`` — this is a private API; if
Litestar promotes it to a public surface, the import path should be updated.
"""

import inspect
from typing import TYPE_CHECKING, Any, cast

from litestar import Litestar
from litestar.exceptions import ImproperlyConfiguredException
from litestar.handlers.base import BaseRouteHandler
from litestar.utils.helpers import get_name
from litestar.utils.sync import ensure_async_callable

from litestar_mcp.typing import schema_dump
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from collections.abc import Callable

    from litestar._kwargs import KwargsModel
    from litestar._kwargs.dependencies import Dependency
    from litestar.connection import ASGIConnection


# Subset of Litestar's RESERVED_KWARGS that represent genuinely connection-
# scoped resources we cannot satisfy outside an ASGI request.
#
# Note on ``socket``: Litestar uses this for the websocket connection on
# websocket handlers. MCP tools are always discovered from HTTP route
# handlers (``@get`` / ``@post`` / controllers), so a handler declaring
# ``socket`` would already be rejected at route registration time and
# cannot reach the executor. Listing it here anyway is belt-and-braces so
# that, if the discovery surface ever widens, the executor's refusal of
# connectionless invocation remains correct.
_UNSATISFIABLE_RESERVED_KWARGS = frozenset({"request", "socket", "scope", "state", "headers", "cookies", "query"})

# Reserved kwargs that, in a normal Litestar route, represent the parsed
# request body. MCP tool input arrives as a JSON-RPC ``arguments`` map,
# not an HTTP body, so for MCP tools we explicitly route these names
# through ``tool_args`` — i.e. a handler with ``data: dict[str, Any]`` is
# valid MCP shape and receives ``tool_args["data"]``. On the HTTP path we
# must pre-populate these slots BEFORE calling Litestar's dependency
# resolver, otherwise Litestar's body extractor would try to parse the
# JSON-RPC envelope as the handler's body.
_REQUEST_BODY_RESERVED_KWARGS = frozenset({"data", "body", "form"})


class NotCallableWithoutConnectionError(ImproperlyConfiguredException):
    """Raised when a tool requires an ASGI connection that is not available."""

    def __init__(self, handler_name: str, parameter_name: str) -> None:
        """Initialize the exception.

        Args:
            handler_name: Name of the handler that cannot be called.
            parameter_name: Name of the parameter causing the issue.
        """
        super().__init__(
            f"Tool '{handler_name}' requires an ASGI connection: its dependency "
            f"'{parameter_name}' needs request context (e.g. a plugin-registered "
            f"session, request, or connection-scoped resource). Use the HTTP "
            f"transport to invoke this tool, or remove the connection-scoped dependency."
        )


def _build_kwargs_model(handler: Any) -> "KwargsModel | None":
    """Build a ``KwargsModel`` for the handler, scoped to MCP execution.

    The parameter is typed ``Any`` (not ``BaseRouteHandler``) because the
    unit tests exercise the fallback path by passing a raw function in
    place of a handler. At runtime we return ``None`` in that case; for
    genuine ``BaseRouteHandler`` instances, any failure inside
    ``create_kwargs_model`` propagates so misconfiguration is surfaced
    loudly instead of being swallowed as "no dependencies".
    """
    if not isinstance(handler, BaseRouteHandler):
        return None
    return handler.create_kwargs_model(path_parameters={})


async def _call_provider_without_connection(
    dependency: "Dependency",
    resolved: "dict[str, Any]",
    handler_name: str,
) -> Any:
    """Invoke a single ``Dependency`` node without an ASGI connection.

    Kwargs for the provider are drawn from already-resolved deps and the
    provider's own defaults. Any kwarg that is neither resolved nor has a
    default means the provider genuinely needs request context we can't
    supply — we raise ``NotCallableWithoutConnectionError`` naming the dep.

    Exceptions raised inside the provider itself are **not** rewritten. A
    ``KeyError`` in user logic must surface as a ``KeyError``, not a
    misleading "requires request context" error. Only the specific shape
    of failure that means "I need kwargs I didn't get" is translated.
    """
    provide = dependency.provide

    # Generator-based providers express a setup/teardown lifecycle
    # (``yield resource`` → cleanup after yield). In a real request the
    # cleanup is driven by Litestar's ``DependencyCleanupGroup`` when the
    # response finishes. MCP tool execution without a connection has no
    # such lifecycle, so running the setup side-effect would leave
    # teardown to interpreter GC. Reject upfront, before invoking the
    # provider, so no side effects leak.
    if provide.has_sync_generator_dependency or provide.has_async_generator_dependency:
        raise NotCallableWithoutConnectionError(handler_name, dependency.key)

    parsed_sig = provide.parsed_fn_signature

    kwargs: dict[str, Any] = {}
    for param_name, field in parsed_sig.parameters.items():
        if param_name in resolved:
            kwargs[param_name] = resolved[param_name]
        elif field.has_default:
            # Provider's own default will apply — skip.
            continue
        else:
            raise NotCallableWithoutConnectionError(handler_name, dependency.key)

    return await provide(**kwargs)


async def _resolve_dependencies_via_litestar(
    handler: BaseRouteHandler,
    handler_name: str,
) -> "dict[str, Any]":
    """Resolve the handler's dependencies using Litestar's ``KwargsModel``.

    ``KwargsModel.dependency_batches`` is a pre-computed, topologically
    sorted ``list[set[Dependency]]`` containing only the providers the
    handler actually consumes (directly or transitively). Litestar has
    already done the filtering, cycle detection, and ordering; we only
    need to invoke each provider in batch order.

    If the handler (or any transitively consumed dep) needs a reserved
    framework kwarg such as ``request``, ``state``, or ``scope``, we raise
    ``NotCallableWithoutConnectionError`` up front using Litestar's own
    ``expected_reserved_kwargs`` as the source of truth.
    """
    kwargs_model = _build_kwargs_model(handler)
    if kwargs_model is None:
        return {}

    # Only reject on reserved kwargs that represent connection-scoped
    # resources (request, headers, etc). Request-body names (``data``,
    # ``body``, ``form``) are left alone — the shared ``_fill_tool_args``
    # step below maps them from ``tool_args`` just like any other parameter.
    unsatisfiable = kwargs_model.expected_reserved_kwargs & _UNSATISFIABLE_RESERVED_KWARGS
    if unsatisfiable:
        first = next(iter(sorted(unsatisfiable)))
        raise NotCallableWithoutConnectionError(handler_name, first)

    resolved: dict[str, Any] = {}
    for batch in kwargs_model.dependency_batches:
        for dependency in batch:
            resolved[dependency.key] = await _call_provider_without_connection(dependency, resolved, handler_name)

    return resolved


def _fill_tool_args(
    fn: "Callable[..., Any]",
    resolved: "dict[str, Any]",
    tool_args: "dict[str, Any]",
) -> "dict[str, Any]":
    """Build the final ``call_args`` for the handler function.

    Drops any resolved keys the handler does not declare (transitive deps),
    then layers in matching tool arguments, and finally raises ``ValueError``
    if any required parameter remains unset. Shared by both execution paths.
    """
    sig = inspect.signature(fn)
    handler_params = set(sig.parameters)
    call_args: dict[str, Any] = {k: v for k, v in resolved.items() if k in handler_params}

    for p_name in sig.parameters:
        if p_name in call_args:
            continue
        if p_name in tool_args:
            call_args[p_name] = tool_args[p_name]

    missing = {
        p_name
        for p_name, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty and p_name not in call_args
    }
    if missing:
        missing_args = ", ".join(sorted(missing))
        error_msg = f"Missing required arguments: {missing_args}"
        raise ValueError(error_msg)

    return call_args


async def _invoke_handler(
    handler: BaseRouteHandler,
    fn: "Callable[..., Any]",
    call_args: "dict[str, Any]",
) -> Any:
    """Dispatch the handler and serialize its result.

    Handles the sync/async/sync-to-thread tri-state and the final
    ``schema_dump`` fallback for non-primitive return values. Shared by
    both execution paths so the dispatch logic cannot drift.
    """
    if getattr(handler, "sync_to_thread", False):
        async_fn = ensure_async_callable(fn)
        result = await async_fn(**call_args)
    elif inspect.iscoroutinefunction(fn):
        result = cast("Any", await fn(**call_args))  # pyright: ignore[reportGeneralTypeIssues]
    else:
        result = fn(**call_args)

    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        return schema_dump(result)
    return result  # pyright: ignore


def check_cli_compatibility(handler: Any) -> tuple[bool, str | None]:
    """Check whether a handler can execute without an ASGI connection.

    Performs the same static checks that the connectionless executor would
    apply at runtime — reserved-kwarg detection and generator-provider
    rejection — but without invoking any provider code.

    Args:
        handler: A route handler (or raw function for tests).

    Returns:
        A ``(is_compatible, reason)`` tuple. When compatible, *reason* is
        ``None``; otherwise it is a human-readable explanation suitable for
        CLI display.
    """
    kwargs_model = _build_kwargs_model(handler)
    if kwargs_model is None:
        return True, None

    unsatisfiable = kwargs_model.expected_reserved_kwargs & _UNSATISFIABLE_RESERVED_KWARGS
    if unsatisfiable:
        first = next(iter(sorted(unsatisfiable)))
        return False, f"requires '{first}' (ASGI connection)"

    for batch in kwargs_model.dependency_batches:
        for dependency in batch:
            provide = dependency.provide
            if provide.has_sync_generator_dependency or provide.has_async_generator_dependency:
                return False, f"dependency '{dependency.key}' uses generator provider"

    return True, None


async def _execute_connectionless(
    handler: BaseRouteHandler,
    tool_args: "dict[str, Any]",
) -> Any:
    """Execute a tool without an ASGI connection (CLI path).

    Uses Litestar's ``KwargsModel`` batching to discover and invoke
    handler-consumed providers, but without a live request scope. Any
    reserved framework kwarg (``request``, ``state``, ...) or generator
    provider is rejected via :class:`NotCallableWithoutConnectionError`.
    """
    if isinstance(handler, BaseRouteHandler):
        fn: Callable[..., Any] = get_handler_function(handler)
    else:
        # Test path: raw function passed directly.
        fn = handler

    handler_name = get_name(fn)
    dependencies = await _resolve_dependencies_via_litestar(handler, handler_name)
    call_args = _fill_tool_args(fn, dependencies, tool_args)
    return await _invoke_handler(handler, fn, call_args)


async def _execute_with_connection(
    handler: BaseRouteHandler,
    connection: "ASGIConnection[Any, Any, Any, Any]",
    tool_args: "dict[str, Any]",
) -> Any:
    """Execute a tool against a live ASGI connection.

    This is the HTTP MCP path. It uses Litestar's ``KwargsModel`` to:

    1. Enumerate the reserved framework kwargs the handler declared
       (``request``, ``headers``, ``cookies``, ``query``, ``state``, ``scope``)
       via ``kwargs_model.expected_reserved_kwargs`` and satisfy them
       manually from the connection. Request-body slots (``data``/``body``/
       ``form``) are pre-populated from ``tool_args`` BEFORE calling
       Litestar's resolver, so the resolver's body extractor never tries
       to parse the JSON-RPC envelope as the handler's payload.
    2. Resolve dependency batches against the live connection via
       ``kwargs_model.resolve_dependencies(connection, kwargs)`` — this is
       where plugin-registered deps (``db_engine`` / ``db_session`` / etc.)
       actually flow in, using Litestar's own concurrent TaskGroup batching.
    3. Run the handler inside a ``try``/``finally`` that invokes the
       ``DependencyCleanupGroup`` returned by ``resolve_dependencies``, so
       generator providers' teardown runs even on exceptions.

    Business-logic parameters (everything that is not a plugin dep or a
    reserved framework kwarg) come from ``tool_args``, identical to how the
    connectionless path handles them.

    Known semantics gap — cleanup timing:
        In a normal Litestar route, ``DependencyCleanupGroup.close()`` is
        invoked *after* the response has been fully sent (via the route
        lifecycle hooks). Here we call ``close()`` inside the tool handler's
        ``finally`` block, which fires while the outer ``POST /mcp`` route is
        still running — i.e. BEFORE the MCP response is serialized and
        flushed. For most providers (transaction-scoped sessions, fixture
        resources, etc.) this is indistinguishable. Providers that observe
        "response already sent" state — e.g. ones that commit only after
        successful transmission — would see different timing here than in a
        real route. Plugins affected by this can opt out of MCP callability.
    """
    kwargs_model = handler.create_kwargs_model(path_parameters={})

    fn: Callable[..., Any] = get_handler_function(handler)

    kwargs: dict[str, Any] = {}
    # Pre-populate request-body reserved slots from ``tool_args`` so that
    # Litestar's body extractor (invoked by ``resolve_dependencies``) sees
    # the slot already satisfied and does not attempt to parse the MCP
    # POST body, which is the JSON-RPC envelope rather than the tool
    # payload.
    for body_kwarg in kwargs_model.expected_reserved_kwargs & _REQUEST_BODY_RESERVED_KWARGS:
        if body_kwarg in tool_args:
            kwargs[body_kwarg] = tool_args[body_kwarg]
    # Satisfy only the reserved framework slots the handler actually
    # declared, and only from connection attributes that do NOT touch the
    # request body. Request-body slots were already pre-populated above.
    for reserved in kwargs_model.expected_reserved_kwargs:
        if reserved == "request":
            kwargs["request"] = connection
        elif reserved == "headers":
            kwargs["headers"] = connection.headers
        elif reserved == "cookies":
            kwargs["cookies"] = connection.cookies
        elif reserved == "query":
            kwargs["query"] = connection.query_params
        elif reserved == "state":
            # Mirror Litestar's own ``state_extractor`` byte-for-byte (see
            # ``litestar/_kwargs/extractors.py::state_extractor``). The
            # reserved ``state`` kwarg resolves to the app-level ``State``'s
            # underlying dict, NOT the request-scoped state. Plugin providers
            # read resources the plugin placed on app state during
            # ``on_app_init``. Private-attribute access is intentional — we're
            # copying the framework's own extractor; a regression test in
            # ``tests/test_executor.py`` fails loudly if ``_state`` disappears.
            kwargs["state"] = connection.app.state._state  # noqa: SLF001
        elif reserved == "scope":
            kwargs["scope"] = connection.scope
        # Any other reserved slot is left unset — if the handler actually
        # needs one, ``resolve_dependencies`` or the missing-arg check in
        # ``_fill_tool_args`` will surface it.

    cleanup_group = await kwargs_model.resolve_dependencies(connection=connection, kwargs=kwargs)
    try:
        call_args = _fill_tool_args(fn, kwargs, tool_args)
        return await _invoke_handler(handler, fn, call_args)
    finally:
        await cleanup_group.close()


async def execute_tool(
    handler: BaseRouteHandler,
    _app: Litestar,
    tool_args: "dict[str, Any]",
    *,
    connection: "ASGIConnection[Any, Any, Any, Any] | None" = None,
) -> Any:
    """Execute a route handler as an MCP tool.

    Args:
        handler: The route handler to execute.
        _app: The Litestar app instance. Unused; retained for signature
            stability with existing callers.
        tool_args: A dictionary of arguments to pass to the tool.
        connection: An active ASGI connection (typically the ``Request`` for the
            incoming MCP POST). When provided, the tool is resolved against the
            live request scope, enabling plugin-registered dependencies and
            generator-provider teardown. When ``None`` (the default, used by
            the CLI), falls back to the connectionless resolver.

    Returns:
        The result of the handler execution.

    Raises:
        NotCallableWithoutConnectionError: If the handler requires request-scoped
            dependencies and no connection was provided.
        ValueError: If required arguments are missing.
    """
    if connection is not None:
        return await _execute_with_connection(handler, connection, tool_args)
    return await _execute_connectionless(handler, tool_args)
