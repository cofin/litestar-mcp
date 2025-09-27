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


def _check_unsupported_dependency(dep_name: str, unsupported_cli_deps: "set[str]", fn: Any) -> None:
    """Check if dependency is unsupported in CLI context and raise error if so."""
    if dep_name in unsupported_cli_deps:
        raise NotCallableInCLIContextError(get_name(fn), dep_name)


async def _resolve_dependencies(
    handler: BaseRouteHandler, fn: Any, unsupported_cli_deps: "set[str]"
) -> "dict[str, Any]":
    """Resolve dependencies for the handler, extracted to reduce complexity."""
    dependencies: dict[str, Any] = {}

    try:
        resolved_deps = handler.resolve_dependencies()
        for dep_name, dep_provider in resolved_deps.items():
            _check_unsupported_dependency(dep_name, unsupported_cli_deps, fn)

            # For other dependencies, we execute the dependency provider directly
            try:
                # Provide objects have a dependency property with the actual callable
                if hasattr(dep_provider, "dependency"):
                    dependency_fn = dep_provider.dependency
                    if inspect.iscoroutinefunction(dependency_fn):
                        dependencies[dep_name] = await dependency_fn()
                    else:
                        dependencies[dep_name] = dependency_fn()
                # Fallback: try to call the provider directly
                elif inspect.iscoroutinefunction(dep_provider):
                    dependencies[dep_name] = await dep_provider()
                else:
                    dependencies[dep_name] = dep_provider()
            except Exception as e:
                # If dependency resolution fails, it might be request-dependent
                raise NotCallableInCLIContextError(get_name(fn), dep_name) from e
    except NotCallableInCLIContextError:
        # Re-raise CLI context errors
        raise
    except (AttributeError, TypeError) as e:
        # If resolve_dependencies fails entirely, log and assume no dependencies
        # This can happen in test environments or when handler is not properly configured
        import logging

        logger = logging.getLogger(__name__)
        logger.debug("Failed to resolve dependencies for %s: %s", get_name(fn), e)

    return dependencies


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
    unsupported_cli_deps = {"request", "socket", "headers", "cookies", "query", "body"}

    call_args: dict[str, Any] = {}

    # Resolve dependencies first - extract to helper function to reduce complexity
    dependencies = await _resolve_dependencies(handler, fn, unsupported_cli_deps)

    call_args.update(dependencies)

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
