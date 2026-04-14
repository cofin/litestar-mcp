"""Core execution logic for invoking MCP tools."""

import inspect
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from litestar import Litestar
from litestar.exceptions import ImproperlyConfiguredException
from litestar.handlers.base import BaseRouteHandler
from litestar.utils.helpers import get_name
from litestar.utils.sync import ensure_async_callable

from litestar_mcp.config import MCPConfig
from litestar_mcp.typing import schema_dump
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from collections.abc import Callable

_UNSUPPORTED_CLI_DEPENDENCIES = {"request", "socket", "headers", "cookies", "query", "body"}
_EXECUTION_CONTEXT_PARAMS = {"resolved_user", "user_claims"}


@dataclass
class ToolExecutionContext:
    """Context exposed to dependency providers during tool execution."""

    app: Litestar
    handler: BaseRouteHandler
    tool_args: dict[str, Any]
    user_claims: "dict[str, Any] | None" = None
    resolved_user: Any = None


def _check_unsupported_dependency(dep_name: str, fn: Any) -> None:
    """Check if a dependency is unsupported in CLI context and raise error if so."""
    if dep_name in _UNSUPPORTED_CLI_DEPENDENCIES:
        raise NotCallableInCLIContextError(get_name(fn), dep_name)


async def _call_dependency_provider(dep_provider: Any) -> Any:
    if hasattr(dep_provider, "dependency"):
        dependency_fn = dep_provider.dependency
        if inspect.iscoroutinefunction(dependency_fn):
            return await dependency_fn()
        return dependency_fn()

    if inspect.iscoroutinefunction(dep_provider):
        return await dep_provider()

    if callable(dep_provider):
        return dep_provider()

    return dep_provider


async def _resolve_dependencies(
    handler: BaseRouteHandler,
    fn: Any,
    signature: inspect.Signature,
    pre_resolved_dependencies: "dict[str, Any]",
) -> "dict[str, Any]":
    """Resolve only the dependencies that the handler actually consumes."""
    consumed_dependency_names = set(signature.parameters).difference(pre_resolved_dependencies)
    dependencies: dict[str, Any] = {}

    try:
        resolved_deps = handler.resolve_dependencies()
        for dep_name, dep_provider in resolved_deps.items():
            if dep_name not in consumed_dependency_names:
                continue

            _check_unsupported_dependency(dep_name, fn)

            try:
                dependencies[dep_name] = await _call_dependency_provider(dep_provider)
            except Exception as exc:
                raise NotCallableInCLIContextError(get_name(fn), dep_name) from exc
    except NotCallableInCLIContextError:
        raise
    except (AttributeError, TypeError):
        return {}

    return dependencies


async def _resolve_dependency_provider(
    config: "MCPConfig | None",
    context: ToolExecutionContext,
    exit_stack: AsyncExitStack,
) -> "dict[str, Any]":
    """Resolve context-managed dependencies from the configured provider hook."""
    if config is None or config.dependency_provider is None:
        return {}

    provided = config.dependency_provider(context)
    if hasattr(provided, "__aenter__"):
        resolved = await exit_stack.enter_async_context(provided)
    elif hasattr(provided, "__enter__"):
        resolved = exit_stack.enter_context(provided)
    elif hasattr(provided, "__await__"):
        resolved = await provided
    else:
        resolved = provided

    if resolved is None:
        return {}
    if not isinstance(resolved, dict):
        msg = "dependency_provider must return a mapping of injected keyword arguments"
        raise TypeError(msg)
    return resolved


def _inject_execution_context(
    signature: inspect.Signature,
    *,
    user_claims: "dict[str, Any] | None",
    resolved_user: Any,
) -> "dict[str, Any]":
    """Inject reserved execution context parameters when the handler requests them."""
    injected: dict[str, Any] = {}

    if "user_claims" in signature.parameters and user_claims is not None:
        injected["user_claims"] = user_claims
    if "resolved_user" in signature.parameters and resolved_user is not None:
        injected["resolved_user"] = resolved_user

    return injected


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
    app: Litestar,
    tool_args: "dict[str, Any]",
    *,
    config: "MCPConfig | None" = None,
    user_claims: "dict[str, Any] | None" = None,
    resolved_user: Any = None,
) -> Any:
    """Execute a given route handler with arguments, handling dependency injection.

    Args:
        handler: The route handler to execute.
        app: The Litestar app instance.
        tool_args: A dictionary of arguments to pass to the tool.
        config: Optional MCP configuration for advanced dependency injection hooks.
        user_claims: Optional validated user claims.
        resolved_user: Optional user object derived from the claims.

    Returns:
        The result of the handler execution.

    Raises:
        ValueError: If required arguments are missing.
    """
    try:
        fn: Callable[..., Any] = get_handler_function(handler)
    except AttributeError:
        fn = cast("Callable[..., Any]", handler)

    signature = inspect.signature(fn)
    call_args = _inject_execution_context(signature, user_claims=user_claims, resolved_user=resolved_user)

    for parameter_name in signature.parameters:
        _check_unsupported_dependency(parameter_name, fn)

    async with AsyncExitStack() as exit_stack:
        execution_context = ToolExecutionContext(
            app=app,
            handler=handler,
            tool_args=tool_args,
            user_claims=user_claims,
            resolved_user=resolved_user,
        )
        provided_dependencies = await _resolve_dependency_provider(config, execution_context, exit_stack)
        call_args.update(provided_dependencies)

        dependencies = await _resolve_dependencies(handler, fn, signature, call_args)
        call_args.update(dependencies)

        for parameter_name in signature.parameters:
            if parameter_name in call_args or parameter_name in _EXECUTION_CONTEXT_PARAMS:
                continue
            if parameter_name in tool_args:
                call_args[parameter_name] = tool_args[parameter_name]

        required_params = {
            parameter_name
            for parameter_name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty and parameter_name not in call_args
        }
        if required_params:
            missing_args = ", ".join(sorted(required_params))
            msg = f"Missing required arguments: {missing_args}"
            raise ValueError(msg)

        if getattr(handler, "sync_to_thread", False):
            async_fn = ensure_async_callable(fn)
            result = await async_fn(**call_args)
        elif inspect.iscoroutinefunction(fn):
            result = await fn(**call_args)
        else:
            result = fn(**call_args)

    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        return schema_dump(result)

    return result
