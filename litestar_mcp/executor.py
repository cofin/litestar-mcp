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
from litestar.utils.compat import async_next
from litestar.utils.helpers import get_name
from litestar.utils.sync import ensure_async_callable

from litestar_mcp.typing import schema_dump
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from collections.abc import Callable

    from litestar._kwargs import KwargsModel
    from litestar._kwargs.dependencies import Dependency


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
        value = await provide(**kwargs)
    except NotCallableInCLIContextError:
        raise
    except Exception as e:
        raise NotCallableInCLIContextError(handler_name, dependency.key) from e

    # Generator dependencies: pull the first value. We don't manage a
    # DependencyCleanupGroup the way the normal request pipeline does, so
    # cleanup happens at interpreter GC. For short-lived MCP tool calls
    # this is acceptable; handlers needing precise teardown should not be
    # MCP-callable anyway.
    if provide.has_sync_generator_dependency:
        value = next(value)
    elif provide.has_async_generator_dependency:
        value = await async_next(value)

    return value


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


async def execute_tool(
    handler: BaseRouteHandler,
    _app: Litestar,
    tool_args: "dict[str, Any]",
) -> Any:
    """Execute a route handler as an MCP tool, delegating DI to Litestar.

    Args:
        handler: The route handler to execute.
        _app: The Litestar app instance. Unused; retained for signature
            stability with existing callers.
        tool_args: A dictionary of arguments to pass to the tool.

    Returns:
        The result of the handler execution.

    Raises:
        NotCallableInCLIContextError: If the handler requires request-scoped
            dependencies unavailable in an MCP context.
        ValueError: If required arguments are missing.
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
