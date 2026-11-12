"""Litestar route handler builders for MCP Tool instances."""

import copy
import inspect
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, get_type_hints

from litestar import Request
from litestar.constants import RESERVED_KWARGS
from litestar.di import NamedDependency
from litestar.exceptions import ImproperlyConfiguredException
from litestar.handlers import HTTPRouteHandler, post
from litestar.params import QueryParameter

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.serialization import decode_json
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
) -> tuple[inspect.Signature, dict[str, Any], dict[str, str]]:
    """Build inspect.Signature, __annotations__, and reserved-kwarg alias map for a tool route handler."""
    try:
        hints = get_type_hints(tool.fn, include_extras=True)
    except Exception as exc:
        msg = f"Cannot resolve type hints for tool {tool.name!r}: {exc}"
        raise ImproperlyConfiguredException(msg) from exc

    raw_sig = inspect.signature(tool.fn)
    params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    renamed_params: dict[str, str] = {}
    di_params = tool.di_parameters

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
        if pname == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        di_spec = di_params.get(pname)
        if di_spec is not None:
            if di_spec.source_kind == "litestar":
                litestar_ann = NamedDependency[Any]
                params.append(
                    inspect.Parameter(
                        name=pname,
                        kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        default=di_spec.default,
                        annotation=litestar_ann,
                    )
                )
                annotations[pname] = litestar_ann
            continue

        if tool.has_raw_schema or pname in tool.stripped_parameters:
            continue

        ann = hints.get(pname, param.annotation)
        if ann is inspect.Parameter.empty:
            ann = Any

        raw_props = tool.parameters.get("properties")
        prop_meta = raw_props.get(pname) if isinstance(raw_props, Mapping) else None
        raw_desc = prop_meta.get("description") if isinstance(prop_meta, Mapping) else None
        param_desc = raw_desc if isinstance(raw_desc, str) and raw_desc else None
        query_spec = QueryParameter(name=pname, description=param_desc)

        endpoint_name = pname
        if pname in RESERVED_KWARGS:
            endpoint_name = f"_mcp_param_{pname}"
            renamed_params[endpoint_name] = pname
        endpoint_ann: Any = Annotated[ann, query_spec]

        params.append(
            inspect.Parameter(
                name=endpoint_name,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=param.default,
                annotation=endpoint_ann,
            )
        )
        annotations[endpoint_name] = endpoint_ann

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
    return sig, annotations, renamed_params


async def _merge_raw_body_args(tool: Tool, req: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Decode raw JSON body arguments for `Tool.from_schema` endpoints and merge endpoint kwargs."""
    merged: dict[str, Any] = {}
    if tool.has_raw_schema and req is not None:
        raw_body = await req.body()
        if raw_body:
            decoded = decode_json(raw_body)
            if isinstance(decoded, Mapping):
                merged.update(decoded)
    merged.update(kwargs)
    return merged


def _build_tool_metadata_and_opt(tool: Tool) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build `(metadata, route_opt)` dictionaries preserving explicit tool schemas and descriptors."""
    metadata: dict[str, Any] = {
        "type": "tool",
        "name": tool.name,
        "description": tool.description,
    }
    if tool.has_raw_schema:
        metadata["input_schema"] = copy.deepcopy(tool.parameters)
    if tool.annotations:
        metadata["annotations"] = copy.deepcopy(dict(tool.annotations))
    if tool.title:
        metadata["title"] = tool.title
    if tool.icons:
        metadata["icons"] = [copy.deepcopy(dict(item)) for item in tool.icons]
    if tool.meta:
        metadata["meta"] = copy.deepcopy(dict(tool.meta))
    output_policy = tool.output_schema_policy
    if output_policy is not None:
        metadata["output_schema"] = output_policy

    route_opt: dict[str, Any] = {
        "_mcp_synthesized_tool": True,
        "_mcp_tool_instance": tool,
    }
    if tool.has_raw_schema:
        route_opt["_mcp_raw_tool_args"] = True
    if tool.annotations:
        route_opt["mcp_annotations"] = copy.deepcopy(dict(tool.annotations))
    return metadata, route_opt


def build_controller_tool_handler(tool: Tool) -> HTTPRouteHandler:
    """Return a POST /tools/<name> handler that dispatches a bound controller tool."""
    sig, annotations, renamed_params = _build_tool_endpoint_signature(tool, is_controller=True)

    async def _controller_tool_endpoint(self: Any, **kwargs: Any) -> Any:
        req = kwargs.pop("request", None)
        for endpoint_name, original_name in renamed_params.items():
            if endpoint_name in kwargs:
                kwargs[original_name] = kwargs.pop(endpoint_name)
        call_args = await _merge_raw_body_args(tool, req, kwargs)
        ctx = ToolContext.from_connection(req) if req is not None else None
        bound = tool.__get__(self, type(self))
        return await bound.execute(call_args, context=ctx)

    _controller_tool_endpoint.__name__ = f"_tool_endpoint_{tool.name}"
    _controller_tool_endpoint.__qualname__ = f"_tool_endpoint_{tool.name}"
    _controller_tool_endpoint.__doc__ = tool.description
    _controller_tool_endpoint.__annotations__ = annotations
    setattr(_controller_tool_endpoint, "__signature__", sig)

    metadata, route_opt = _build_tool_metadata_and_opt(tool)
    set_mcp_metadata(_controller_tool_endpoint, metadata)

    handler_deps = {k: v for k, v in tool.dependencies.items() if k in tool.di_parameters}
    return post(
        f"/tools/{tool.name}",
        guards=[require_internal_dispatch],
        dependencies=handler_deps or None,
        include_in_schema=False,
        opt=route_opt,
    )(_controller_tool_endpoint)


def build_tool_route_handler(
    tool: Tool,
    *,
    base_path: str,
    guards: "Sequence[Guard] | None" = None,
    dependencies: "dict[str, Provide] | None" = None,
) -> HTTPRouteHandler:
    """Return a standalone POST <base_path>/internal/tools/<name> handler for a tool."""
    if dependencies:
        tool.register_dependencies(dependencies)
    sig, annotations, renamed_params = _build_tool_endpoint_signature(tool, is_controller=False)

    async def _standalone_tool_endpoint(**kwargs: Any) -> Any:
        req = kwargs.pop("request", None)
        for endpoint_name, original_name in renamed_params.items():
            if endpoint_name in kwargs:
                kwargs[original_name] = kwargs.pop(endpoint_name)
        call_args = await _merge_raw_body_args(tool, req, kwargs)
        ctx = ToolContext.from_connection(req) if req is not None else None
        return await tool.execute(call_args, context=ctx)

    _standalone_tool_endpoint.__name__ = f"_tool_endpoint_{tool.name}"
    _standalone_tool_endpoint.__qualname__ = f"_tool_endpoint_{tool.name}"
    _standalone_tool_endpoint.__doc__ = tool.description
    _standalone_tool_endpoint.__annotations__ = annotations
    setattr(_standalone_tool_endpoint, "__signature__", sig)

    metadata, route_opt = _build_tool_metadata_and_opt(tool)
    set_mcp_metadata(_standalone_tool_endpoint, metadata)

    clean_base = base_path.rstrip("/")
    path = f"{clean_base}/internal/tools/{tool.name}"

    merged_deps: dict[str, Any] = dict(tool.dependencies)
    if dependencies:
        merged_deps.update(dependencies)

    route_guards = [require_internal_dispatch, *(guards or ())]
    return post(
        path=path,
        guards=route_guards,
        dependencies=merged_deps or None,
        include_in_schema=False,
        opt=route_opt,
    )(_standalone_tool_endpoint)
