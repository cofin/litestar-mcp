"""Agent tool abstraction with parameter stripping and concurrent execution."""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_args, get_origin

import anyio

from litestar_mcp.agent.context import ToolContext

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_current_tool_context: ContextVar[ToolContext | None] = ContextVar("current_tool_context", default=None)

CONTEXT_TYPES = frozenset(
    {
        "ToolContext",
        "Request",
        "ASGIConnection",
        "State",
        "Scope",
        "Receive",
        "Send",
    }
)

_BASIC_TYPE_MAP: dict[type[Any], str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    dict: "object",
    list: "array",
}


class RunContextRegistry:
    """Task-local context variable registry for active ToolContext instances."""

    @classmethod
    def get(cls) -> ToolContext | None:
        """Retrieve the active ToolContext for the current async task."""
        return _current_tool_context.get()

    @classmethod
    def set(cls, context: ToolContext | None) -> Token[ToolContext | None]:
        """Set the active ToolContext for the current async task."""
        return _current_tool_context.set(context)

    @classmethod
    def reset(cls, token: Token[ToolContext | None]) -> None:
        """Restore the previous ToolContext using a contextvar token."""
        _current_tool_context.reset(token)

    @classmethod
    @contextmanager
    def bound(cls, context: ToolContext | None) -> Iterator[ToolContext | None]:
        """Context manager that binds a ToolContext for the duration of a block."""
        token = cls.set(context)
        try:
            yield context
        finally:
            cls.reset(token)


def get_current_tool_context() -> ToolContext | None:
    """Get the active ToolContext for the current asynchronous task."""
    return RunContextRegistry.get()


def set_current_tool_context(context: ToolContext | None) -> Token[ToolContext | None]:
    """Set the active ToolContext for the current asynchronous task."""
    return RunContextRegistry.set(context)


def _python_type_to_json_type(annotation: Any) -> dict[str, Any]:
    """Map basic Python annotations to JSON Schema types."""
    if annotation in (inspect.Parameter.empty, Any):
        return {"type": "string"}

    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        if origin is list:
            item_type = _python_type_to_json_type(args[0]) if args else {"type": "string"}
            return {"type": "array", "items": item_type}
        if origin is dict:
            return {"type": "object"}
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _python_type_to_json_type(non_none[0])

    if isinstance(annotation, type):
        for typ, json_typ in _BASIC_TYPE_MAP.items():
            if issubclass(annotation, typ):
                return {"type": json_typ}

    return {"type": "string"}


def _is_context_parameter(param_name: str, param: inspect.Parameter) -> bool:
    """Return whether a function parameter should be stripped and injected from ToolContext."""
    annotation_name = getattr(param.annotation, "__name__", str(param.annotation))
    if annotation_name in CONTEXT_TYPES or "ToolContext" in str(param.annotation):
        return True
    if param_name in {"ctx", "context", "tool_context", "request", "state"}:
        return True
    return isinstance(param.annotation, type) and issubclass(param.annotation, ToolContext)


@dataclass(slots=True)
class Tool:
    """Executable agent tool with runtime reflection and parameter stripping."""

    name: str
    description: str
    fn: Callable[..., Any]
    parameters: dict[str, Any]
    stripped_parameters: set[str] = field(default_factory=set)
    is_agent_tool: bool = True
    bound_instance: Any = None

    @property
    def input_schema(self) -> dict[str, Any]:
        """Return the JSON Schema object for the tool's input parameters."""
        return self.parameters

    @property
    def requires_context(self) -> bool:
        """Return whether the tool requires runtime ToolContext injection."""
        return bool(self.stripped_parameters)

    @property
    def bound_fn(self) -> Callable[..., Any]:
        """Return the underlying callable bound to its owner instance if applicable."""
        if self.bound_instance is not None and hasattr(self.fn, "__get__"):
            bound: Callable[..., Any] = self.fn.__get__(self.bound_instance, type(self.bound_instance))
            return bound
        return self.fn

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Tool:
        """Support descriptor binding when @tool is used on controller methods."""
        if instance is None:
            return self
        return Tool(
            name=self.name,
            description=self.description,
            fn=self.fn,
            parameters=self.parameters,
            stripped_parameters=self.stripped_parameters,
            is_agent_tool=self.is_agent_tool,
            bound_instance=instance,
        )

    def to_json_schema(self) -> dict[str, Any]:
        """Return the JSON schema representation exposed to LLMs."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(
        self,
        arguments: dict[str, Any] | None = None,
        context: ToolContext | None = None,
    ) -> Any:
        """Execute the tool with the provided argument dictionary and optional ToolContext."""
        return await self(**(arguments or {}), __tool_context__=context)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Invoke tool injecting any stripped parameters from active ToolContext."""
        ctx = kwargs.pop("__tool_context__", None) or get_current_tool_context()
        bound_kwargs = dict(kwargs)

        for param_name in self.stripped_parameters:
            if param_name not in bound_kwargs and ctx is not None:
                if param_name in {"ctx", "context", "tool_context"}:
                    bound_kwargs[param_name] = ctx
                elif param_name == "user":
                    bound_kwargs[param_name] = ctx.user
                elif param_name in ("tenant_id", "tenant"):
                    bound_kwargs[param_name] = ctx.tenant_id
                elif param_name == "request":
                    bound_kwargs[param_name] = ctx.request
                elif param_name == "state":
                    bound_kwargs[param_name] = ctx.state

        target_fn = (
            self.fn if (args and self.bound_instance is not None and args[0] is self.bound_instance) else self.bound_fn
        )
        with RunContextRegistry.bound(ctx):
            if inspect.iscoroutinefunction(target_fn):
                return await target_fn(*args, **bound_kwargs)
            return target_fn(*args, **bound_kwargs)


def tool(
    name: str | None = None,
    *,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """Decorator to convert a callable into an agent Tool with parameter stripping."""

    def decorator(fn: Callable[..., Any]) -> Tool:
        tool_name = name or fn.__name__
        tool_desc = description or inspect.cleandoc(fn.__doc__ or "")
        sig = inspect.signature(fn)

        properties: dict[str, Any] = {}
        required: list[str] = []
        stripped: set[str] = set()

        for param_name, param in sig.parameters.items():
            if param_name == "self" or param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            if _is_context_parameter(param_name, param):
                stripped.add(param_name)
                continue

            param_schema = _python_type_to_json_type(param.annotation)
            properties[param_name] = param_schema
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        parameters_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            parameters_schema["required"] = required

        instance = Tool(
            name=tool_name,
            description=tool_desc,
            fn=fn,
            parameters=parameters_schema,
            stripped_parameters=stripped,
        )
        setattr(fn, "__agent_tool__", True)
        setattr(fn, "__tool_instance__", instance)
        return instance

    return decorator


_TOOL_EXCEPTIONS: tuple[type[BaseException], ...] = (Exception,)


async def execute_parallel(
    tools: list[tuple[Tool, dict[str, Any]]],
    context: ToolContext | None = None,
) -> list[Any]:
    """Execute multiple (Tool, arguments) pairs concurrently using an anyio task group."""
    if not tools:
        return []

    results: list[Any] = [None] * len(tools)

    async def _run_at(index: int, target_tool: Tool, arguments: dict[str, Any]) -> None:
        try:
            results[index] = await target_tool.execute(arguments, context=context)
        except _TOOL_EXCEPTIONS as exc:
            results[index] = {
                "error": f"Error executing tool '{target_tool.name}': {exc}",
                "is_error": True,
            }

    async with anyio.create_task_group() as tg:
        for idx, (target_tool, args) in enumerate(tools):
            tg.start_soon(_run_at, idx, target_tool, args)

    return results


async def execute_tools_in_parallel(
    tool_calls: list[dict[str, Any]],
    tools: dict[str, Tool],
    context: ToolContext | None = None,
) -> list[dict[str, Any]]:
    """Execute multiple tool invocations concurrently with error trapping using anyio."""
    if not tool_calls:
        return []

    results: list[dict[str, Any]] = [{} for _ in tool_calls]

    async def _invoke_at(index: int, call: dict[str, Any]) -> None:
        call_id = call.get("call_id") or call.get("id", "")
        tool_name = call.get("name") or call.get("tool_name", "")
        args = call.get("arguments") or {}

        target_tool = tools.get(tool_name)
        if target_tool is None:
            results[index] = {
                "call_id": call_id,
                "name": tool_name,
                "content": f"Error: Tool '{tool_name}' not found.",
                "is_error": True,
            }
            return

        try:
            res = await target_tool(**args, __tool_context__=context)
            content_str = str(res) if not isinstance(res, str) else res
        except _TOOL_EXCEPTIONS as exc:
            results[index] = {
                "call_id": call_id,
                "name": tool_name,
                "content": f"Error executing tool '{tool_name}': {exc}",
                "is_error": True,
            }
        else:
            results[index] = {
                "call_id": call_id,
                "name": tool_name,
                "content": content_str,
                "is_error": False,
            }

    async with anyio.create_task_group() as tg:
        for idx, call in enumerate(tool_calls):
            tg.start_soon(_invoke_at, idx, call)

    return results


__all__ = (
    "CONTEXT_TYPES",
    "RunContextRegistry",
    "Tool",
    "execute_parallel",
    "execute_tools_in_parallel",
    "get_current_tool_context",
    "set_current_tool_context",
    "tool",
)
