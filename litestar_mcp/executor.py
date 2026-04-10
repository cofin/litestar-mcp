"""Core execution logic for invoking MCP tools."""

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


def _provider_callable(provider: Any) -> "Callable[..., Any] | None":
    """Return the underlying callable of a registry entry, or None if opaque.

    Litestar `Provide` objects expose the actual callable via `.dependency`.
    Some registry entries may already be bare callables (test doubles, edge
    cases). Anything else we can't introspect is treated as having no deps.
    """
    if hasattr(provider, "dependency"):
        return provider.dependency  # type: ignore[no-any-return]
    if callable(provider):
        return provider
    return None


def _collect_consumed_dependencies(
    fn: "Callable[..., Any]",
    registry: "dict[str, Any]",
) -> "set[str]":
    """Return the transitive closure of dependency names consumed by fn.

    Walks fn's signature parameters; for each parameter name that exists in
    `registry`, adds it to the consumed set and recursively walks the
    provider's own signature. Parameters not in the registry are ignored
    (they are tool arguments, `self`, or reserved framework parameters —
    the caller handles those separately).

    Cycles are broken by tracking visited names.
    """
    consumed: set[str] = set()
    stack: list[Callable[..., Any]] = [fn]
    visited_callables: set[int] = set()

    while stack:
        current = stack.pop()
        if id(current) in visited_callables:
            continue
        visited_callables.add(id(current))

        try:
            params = inspect.signature(current).parameters
        except (TypeError, ValueError):
            # Builtins or C-implemented callables may not be introspectable.
            continue

        for param_name in params:
            if param_name in consumed:
                continue
            if param_name not in registry:
                continue
            consumed.add(param_name)
            provider_fn = _provider_callable(registry[param_name])
            if provider_fn is not None:
                stack.append(provider_fn)

    return consumed


def _check_unsupported_dependency(dep_name: str, unsupported_cli_deps: "set[str]", fn: Any) -> None:
    """Check if dependency is unsupported in CLI context and raise error if so."""
    if dep_name in unsupported_cli_deps:
        raise NotCallableInCLIContextError(get_name(fn), dep_name)


async def _resolve_dependencies(
    handler: BaseRouteHandler,
    fn: Any,
    unsupported_cli_deps: "set[str]",
) -> "dict[str, Any]":
    """Resolve only the dependencies the handler transitively consumes.

    Mirrors Litestar's DI semantics: walks the handler signature outward,
    calling only providers whose names appear (directly or transitively)
    in the consumed closure. Providers are resolved dependency-first so
    that a provider needing `foo` receives an already-resolved `foo` kwarg.
    """
    try:
        registry = handler.resolve_dependencies()
    except (AttributeError, TypeError) as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.debug("Failed to resolve dependencies for %s: %s", get_name(fn), e)
        return {}

    consumed = _collect_consumed_dependencies(fn, registry)

    # Reserved-name check runs against the CONSUMED set only.
    for dep_name in consumed:
        _check_unsupported_dependency(dep_name, unsupported_cli_deps, fn)

    # Topological resolution: repeatedly resolve any consumed provider whose
    # own kwargs are either already resolved, not in the registry (tool args
    # or framework params we can't satisfy), or have defaults.
    resolved: dict[str, Any] = {}
    remaining = set(consumed)

    while remaining:
        progress = False
        for dep_name in list(remaining):
            provider = registry[dep_name]
            provider_fn = _provider_callable(provider)
            if provider_fn is None:
                raise NotCallableInCLIContextError(get_name(fn), dep_name)

            try:
                provider_sig = inspect.signature(provider_fn)
            except (TypeError, ValueError):
                provider_sig = None

            kwargs: dict[str, Any] = {}
            ready = True
            if provider_sig is not None:
                for p_name, p in provider_sig.parameters.items():
                    if p_name in resolved:
                        kwargs[p_name] = resolved[p_name]
                    elif p_name in registry and p_name in consumed:
                        # Dependency on another consumed provider — wait.
                        ready = False
                        break
                    elif p.default is not inspect.Parameter.empty:
                        continue
                    else:
                        # Unresolvable kwarg with no default — this provider
                        # genuinely needs request context we don't have.
                        raise NotCallableInCLIContextError(get_name(fn), dep_name)

            if not ready:
                continue

            try:
                if inspect.iscoroutinefunction(provider_fn):
                    resolved[dep_name] = await provider_fn(**kwargs)
                else:
                    resolved[dep_name] = provider_fn(**kwargs)
            except NotCallableInCLIContextError:
                raise
            except Exception as e:
                raise NotCallableInCLIContextError(get_name(fn), dep_name) from e

            remaining.discard(dep_name)
            progress = True

        if not progress:
            # Cycle or unsatisfiable graph — fail with the first stuck dep.
            stuck = next(iter(remaining))
            raise NotCallableInCLIContextError(get_name(fn), stuck)

    return resolved


class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a tool is not callable from the CLI due to its dependencies."""

    def __init__(self, handler_name: str, parameter_name: str) -> None:
        """Initialize the exception.

        Args:
            handler_name: Name of the handler that cannot be called.
            parameter_name: Name of the parameter causing the issue.
        """
        super().__init__(
            f"Tool '{handler_name}' cannot be called from the CLI because it depends on the request-scoped "
            f"dependency '{parameter_name}', which is not available in a CLI context."
        )


async def execute_tool(
    handler: BaseRouteHandler,
    _app: Litestar,  # Renamed to indicate unused
    tool_args: "dict[str, Any]",
) -> Any:
    """Execute a given route handler with arguments, handling dependency injection.

    Args:
        handler: The route handler to execute.
        app: The Litestar app instance.
        tool_args: A dictionary of arguments to pass to the tool.

    Returns:
        The result of the handler execution.

    Raises:
        ValueError: If required arguments are missing.
    """
    try:
        fn: Callable[..., Any] = get_handler_function(handler)
    except AttributeError:
        # Fallback for test cases where handler might be a raw function
        fn = handler

    sig = inspect.signature(fn)

    # Dependencies that are not available in a CLI context
    unsupported_cli_deps = {
        "request",
        "socket",
        "headers",
        "cookies",
        "query",
        "body",
        "state",
        "scope",
    }

    call_args: dict[str, Any] = {}

    # Resolve dependencies first - extract to helper function to reduce complexity
    dependencies = await _resolve_dependencies(handler, fn, unsupported_cli_deps)

    # Only inject resolved dependencies that the handler itself declares.
    # Transitive deps (e.g. `role` consumed by `provide_user` but not by the
    # handler directly) are needed for resolution but must NOT be forwarded.
    handler_params = set(sig.parameters)
    call_args.update({k: v for k, v in dependencies.items() if k in handler_params})

    # Check for unsupported CLI dependencies in function parameters
    for p_name in sig.parameters:
        _check_unsupported_dependency(p_name, unsupported_cli_deps, fn)

    # Map tool arguments to function parameters
    for p_name in sig.parameters:
        if p_name in call_args:
            continue
        if p_name in tool_args:
            # Basic type coercion could be added here if needed
            call_args[p_name] = tool_args[p_name]

    # Check for missing arguments
    required_params = {
        p_name
        for p_name, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty and p_name not in call_args
    }
    missing = required_params - set(call_args.keys())
    if missing:
        missing_args = ", ".join(sorted(missing))
        error_msg = f"Missing required arguments: {missing_args}"
        raise ValueError(error_msg)

    # Execute the handler
    if getattr(handler, "sync_to_thread", False):
        # For sync handlers that need to run in thread

        async_fn = ensure_async_callable(fn)
        result = await async_fn(**call_args)
    elif inspect.iscoroutinefunction(fn):
        # For async handlers
        result = cast("Any", await fn(**call_args))  # pyright: ignore[reportGeneralTypeIssues]
    else:
        # For sync handlers
        result = fn(**call_args)

    # Convert schema models to dicts for serialization
    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        return schema_dump(result)

    return result  # pyright: ignore
