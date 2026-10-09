"""Litestar route handler builders for MCP Tool instances."""

import inspect
from typing import TYPE_CHECKING, Any, get_type_hints

from litestar import Request
from litestar.exceptions import ImproperlyConfiguredException
from litestar.handlers import HTTPRouteHandler, post

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.tools import Tool
from litestar_mcp.mcp.executor import require_internal_dispatch
from litestar_mcp.utils import set_mcp_metadata

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litestar.di import Provide
    from litestar.types import Guard

__all__ = (
    "build_controller_tool_handler",
    "build_tool_route_handler",
)


def _build_tool_endpoint_signature(
    tool: Tool,
    *,
    is_controller: bool,
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Build inspect.Signature and __annotations__ for a tool route handler."""
    try:
        hints = get_type_hints(tool.fn, include_extras=True)
    except Exception as exc:
        msg = f"Cannot resolve type hints for tool {tool.name!r}: {exc}"
        raise ImproperlyConfiguredException(msg) from exc

    raw_sig = inspect.signature(tool.fn)
    params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    if is_controller:
        params.append(
            inspect.Parameter(
                name="self",
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=Any,
            )
        )
        annotations["self"] = Any

    for pname, param in raw_sig.parameters.items():
        if pname == "self" or pname in tool.stripped_parameters:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        ann = hints.get(pname, param.annotation)
        if ann is inspect.Parameter.empty:
            ann = Any
        params.append(
            inspect.Parameter(
                name=pname,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=param.default,
                annotation=ann,
            )
        )
        annotations[pname] = ann

    params.append(
        inspect.Parameter(
            name="request",
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=Request,
        )
    )
    annotations["request"] = Request

    ret_ann = hints.get("return", Any)
    if ret_ann is inspect.Parameter.empty:
        ret_ann = Any
    annotations["return"] = ret_ann

    sig = inspect.Signature(parameters=params, return_annotation=ret_ann)
    return sig, annotations


def build_controller_tool_handler(tool: Tool) -> HTTPRouteHandler:
    """Return a POST /tools/<name> handler that dispatches a bound controller tool."""
    sig, annotations = _build_tool_endpoint_signature(tool, is_controller=True)

    async def _controller_tool_endpoint(self: Any, **kwargs: Any) -> Any:
        req = kwargs.pop("request", None)
        ctx = ToolContext.from_connection(req) if req is not None else None
        bound = tool.__get__(self, type(self))
        return await bound.execute(kwargs, context=ctx)

    _controller_tool_endpoint.__name__ = f"_tool_endpoint_{tool.name}"
    _controller_tool_endpoint.__qualname__ = f"_tool_endpoint_{tool.name}"
    _controller_tool_endpoint.__doc__ = tool.description
    _controller_tool_endpoint.__annotations__ = annotations
    setattr(_controller_tool_endpoint, "__signature__", sig)

    set_mcp_metadata(
        _controller_tool_endpoint,
        {"type": "tool", "name": tool.name, "description": tool.description},
    )

    return post(
        f"/tools/{tool.name}",
        guards=[require_internal_dispatch],
        include_in_schema=False,
    )(_controller_tool_endpoint)


def build_tool_route_handler(
    tool: Tool,
    *,
    base_path: str,
    guards: "Sequence[Guard] | None" = None,
    dependencies: "dict[str, Provide] | None" = None,
) -> HTTPRouteHandler:
    """Return a standalone POST <base_path>/internal/tools/<name> handler for a tool."""
    sig, annotations = _build_tool_endpoint_signature(tool, is_controller=False)

    async def _standalone_tool_endpoint(**kwargs: Any) -> Any:
        req = kwargs.pop("request", None)
        ctx = ToolContext.from_connection(req) if req is not None else None
        return await tool.execute(kwargs, context=ctx)

    _standalone_tool_endpoint.__name__ = f"_tool_endpoint_{tool.name}"
    _standalone_tool_endpoint.__qualname__ = f"_tool_endpoint_{tool.name}"
    _standalone_tool_endpoint.__doc__ = tool.description
    _standalone_tool_endpoint.__annotations__ = annotations
    setattr(_standalone_tool_endpoint, "__signature__", sig)

    set_mcp_metadata(
        _standalone_tool_endpoint,
        {"type": "tool", "name": tool.name, "description": tool.description},
    )

    clean_base = base_path.rstrip("/")
    path = f"{clean_base}/internal/tools/{tool.name}"

    route_guards = [require_internal_dispatch, *(guards or ())]
    return post(
        path=path,
        guards=route_guards,
        dependencies=dependencies,
        include_in_schema=False,
    )(_standalone_tool_endpoint)
