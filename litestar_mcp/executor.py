"""Core execution logic for invoking MCP tools.

Dependency resolution is delegated to Litestar's own ``KwargsModel`` machinery
so that the filtering (only handler-consumed deps), transitive walking, and
topological ordering are all handled by the framework we're piggybacking on.
We intentionally reach into ``litestar._kwargs`` — see upstream issue to
promote this to a public API.
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
# scoped resources we cannot satisfy outside an ASGI request. Litestar's own
# RESERVED_KWARGS set also includes "data" and "body" (the request payload),
# but for MCP tools the payload IS the tool arguments, so those are handled
# by the normal tool_args mapping rather than raised as errors.
_UNSATISFIABLE_RESERVED_KWARGS = frozenset({"request", "socket", "scope", "state", "headers", "cookies", "query"})


class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a tool is not callable from the CLI due to its dependencies."""

    def __init__(self, handler_name: str, parameter_name: str) -> None:
        """Initialize the exception.

        Args:
            handler_name: Name of the handler that cannot be called.
            parameter_name: Name of the parameter causing the issue.
        """
        super().__init__(
            f"Tool '{handler_name}' cannot be executed via MCP: its dependency "
            f"'{parameter_name}' requires request context (e.g. a plugin-registered "
            f"session, request, or connection-scoped resource). Tools that need "
            f"such resources are not currently MCP-callable."
        )


def _build_kwargs_model(handler: BaseRouteHandler) -> "KwargsModel | None":
    """Build a ``KwargsModel`` for the handler, scoped to MCP execution.

    MCP tools do not have URL path parameters, so an empty dict is passed.
    Returns ``None`` for test doubles or mocks that don't support
    ``create_kwargs_model`` (e.g. a raw function passed in place of a
    ``BaseRouteHandler``).
    """
    try:
        return handler.create_kwargs_model(path_parameters={})  # type: ignore[arg-type]
    except (AttributeError, TypeError):
        return None


async def _call_provider_without_connection(
    dependency: "Dependency",
    resolved: "dict[str, Any]",
    handler_name: str,
) -> Any:
    """Invoke a single ``Dependency`` node without an ASGI connection.

    Kwargs for the provider are drawn from already-resolved deps and the
    provider's own defaults. Any kwarg that is neither resolved nor has a
    default means the provider genuinely needs request context we can't
    supply — we raise ``NotCallableInCLIContextError`` naming the dep.
    """
    provide = dependency.provide

    # Generator-based providers express a setup/teardown lifecycle
    # (``yield resource`` → cleanup after yield). In a real request the
    # cleanup is driven by Litestar's ``DependencyCleanupGroup`` when the
    # response finishes. MCP tool execution has no such lifecycle, so
    # running the setup side-effect would leave teardown to interpreter
    # GC — nondeterministic, potentially after the MCP response has
    # already been returned. Reject upfront, before invoking the provider,
    # so no side effects leak.
    if provide.has_sync_generator_dependency or provide.has_async_generator_dependency:
        raise NotCallableInCLIContextError(handler_name, dependency.key)

    parsed_sig = provide.parsed_fn_signature

    kwargs: dict[str, Any] = {}
    for param_name, field in parsed_sig.parameters.items():
        if param_name in resolved:
            kwargs[param_name] = resolved[param_name]
        elif field.has_default:
            # Provider's own default will apply — skip.
            continue
        else:
            raise NotCallableInCLIContextError(handler_name, dependency.key)

    try:
        return await provide(**kwargs)
    except Exception as e:
        raise NotCallableInCLIContextError(handler_name, dependency.key) from e


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
    ``NotCallableInCLIContextError`` up front using Litestar's own
    ``expected_reserved_kwargs`` as the source of truth.
    """
    kwargs_model = _build_kwargs_model(handler)
    if kwargs_model is None:
        return {}

    # Only reject on reserved kwargs that represent connection-scoped
    # resources (request, headers, etc). Request-payload names (data, body)
    # are handled by the tool_args mapping in execute_tool.
    unsatisfiable = kwargs_model.expected_reserved_kwargs & _UNSATISFIABLE_RESERVED_KWARGS
    if unsatisfiable:
        first = next(iter(sorted(unsatisfiable)))
        raise NotCallableInCLIContextError(handler_name, first)

    resolved: dict[str, Any] = {}
    for batch in kwargs_model.dependency_batches:
        for dependency in batch:
            resolved[dependency.key] = await _call_provider_without_connection(dependency, resolved, handler_name)

    return resolved


async def _execute_connectionless(
    handler: BaseRouteHandler,
    tool_args: "dict[str, Any]",
) -> Any:
    """Execute a tool without an ASGI connection (CLI path).

    Uses Litestar's ``KwargsModel`` batching to discover and invoke
    handler-consumed providers, but without a live request scope. Any
    reserved framework kwarg (``request``, ``state``, ...) or generator
    provider is rejected via :class:`NotCallableInCLIContextError`.
    """
    try:
        fn: Callable[..., Any] = get_handler_function(handler)
    except AttributeError:
        # Fallback for test cases where handler might be a raw function.
        fn = handler

    handler_name = get_name(fn)
    sig = inspect.signature(fn)

    dependencies = await _resolve_dependencies_via_litestar(handler, handler_name)

    # Forward only the deps the handler itself declares. Transitive
    # intermediaries (e.g. `role` consumed by `provide_user` which is consumed
    # by the handler) are resolved so providers can run, but must NOT be
    # passed as kwargs to the handler — it only accepts its own parameters.
    handler_params = set(sig.parameters)
    call_args: dict[str, Any] = {k: v for k, v in dependencies.items() if k in handler_params}

    # Map tool arguments to any remaining handler parameters.
    for p_name in sig.parameters:
        if p_name in call_args:
            continue
        if p_name in tool_args:
            call_args[p_name] = tool_args[p_name]

    # Check for missing required arguments.
    missing = {
        p_name
        for p_name, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty and p_name not in call_args
    }
    if missing:
        missing_args = ", ".join(sorted(missing))
        error_msg = f"Missing required arguments: {missing_args}"
        raise ValueError(error_msg)

    # Execute the handler.
    if getattr(handler, "sync_to_thread", False):
        async_fn = ensure_async_callable(fn)
        result = await async_fn(**call_args)
    elif inspect.iscoroutinefunction(fn):
        result = cast("Any", await fn(**call_args))  # pyright: ignore[reportGeneralTypeIssues]
    else:
        result = fn(**call_args)

    # Convert schema models to dicts for serialization.
    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        return schema_dump(result)

    return result  # pyright: ignore


async def _execute_with_connection(
    handler: BaseRouteHandler,
    connection: "ASGIConnection[Any, Any, Any, Any]",
    tool_args: "dict[str, Any]",
) -> Any:
    """Execute a tool against a live ASGI connection.

    This is the HTTP MCP path. It uses Litestar's ``KwargsModel`` to:

    1. Enumerate the reserved framework kwargs the handler declared
       (``request``, ``headers``, ``cookies``, ``query``, ``state``, ``scope``)
       via ``kwargs_model.expected_reserved_kwargs`` and satisfy them manually
       from the connection. We deliberately skip ``data``/``body`` — the MCP
       POST body is a JSON-RPC envelope, not the tool's input.
    2. Resolve dependency batches against the live connection via
       ``kwargs_model.resolve_dependencies(connection, kwargs)`` — this is
       where plugin-registered deps (``db_engine`` / ``db_session`` / etc.)
       actually flow in, using Litestar's own concurrent TaskGroup batching.
    3. Run the handler inside a ``try``/``finally`` that invokes the
       ``DependencyCleanupGroup`` returned by ``resolve_dependencies``, so
       generator providers' teardown runs even on exceptions.

    Business-logic parameters (everything that is not a plugin dep or a
    reserved framework kwarg) come from ``tool_args``, identical to how the
    connectionless path handles them. This keeps existing tools like
    ``analyze(data: dict[str, Any])`` working unchanged.

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
    kwargs_model = handler.create_kwargs_model(path_parameters={})  # type: ignore[arg-type]

    kwargs: dict[str, Any] = {}
    # Satisfy only the reserved framework slots the handler actually
    # declared, and only from connection attributes that do NOT touch the
    # request body. `data` and `body` are intentionally omitted — tool
    # input comes from `tool_args`.
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
            # copying the framework's own extractor; if Litestar renames
            # ``_state``, both their code and ours break at the same time.
            kwargs["state"] = connection.app.state._state  # noqa: SLF001
        elif reserved == "scope":
            kwargs["scope"] = connection.scope
        # Any other reserved slot (data, body, socket, form, ...) is left
        # unset — if the handler actually needs one, resolve_dependencies or
        # the missing-arg check below will surface it clearly.

    cleanup_group = await kwargs_model.resolve_dependencies(connection=connection, kwargs=kwargs)
    try:
        fn: Callable[..., Any] = get_handler_function(handler)
        sig = inspect.signature(fn)
        # Fill remaining handler parameters from the MCP tool arguments.
        for p_name in sig.parameters:
            if p_name in kwargs:
                continue
            if p_name in tool_args:
                kwargs[p_name] = tool_args[p_name]
        # Forward only keys that are actual handler parameters. KwargsModel
        # may surface transitive dep values in ``kwargs`` that providers
        # needed but the handler itself does not accept.
        handler_params = set(sig.parameters)
        call_args = {k: v for k, v in kwargs.items() if k in handler_params}
        missing = {
            p_name
            for p_name, p in sig.parameters.items()
            if p.default is inspect.Parameter.empty and p_name not in call_args
        }
        if missing:
            error_msg = f"Missing required arguments: {', '.join(sorted(missing))}"
            raise ValueError(error_msg)
        if getattr(handler, "sync_to_thread", False):
            result = await ensure_async_callable(fn)(**call_args)
        elif inspect.iscoroutinefunction(fn):
            result = cast("Any", await fn(**call_args))  # pyright: ignore[reportGeneralTypeIssues]
        else:
            result = fn(**call_args)
    finally:
        await cleanup_group.close()
    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        return schema_dump(result)
    return result


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
        NotCallableInCLIContextError: If the handler requires request-scoped
            dependencies and no connection was provided.
        ValueError: If required arguments are missing.
    """
    if connection is not None:
        return await _execute_with_connection(handler, connection, tool_args)
    return await _execute_connectionless(handler, tool_args)
