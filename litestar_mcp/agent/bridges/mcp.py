"""MCP protocol bridge for Agent and AgentGroup instances."""

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import httpx2
from litestar import Litestar, Request
from litestar.handlers import BaseRouteHandler

from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.schema_builder import cached_schema_for_handler, unwrap_root_object_schema
from litestar_mcp.core.tools import Tool, current_tool_context
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.content import (
    MCPBlobResource,
    MCPInputRequiredResult,
    MCPResourceLink,
    MCPToolResult,
    normalize_content_block,
    normalize_content_blocks,
)
from litestar_mcp.mcp.executor import MCPToolErrorResult, blank_http_scope, execute_tool
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.prompt_controller import PromptController
from litestar_mcp.mcp.registry import (
    resolve_descriptor_fields,
    resolve_tool_annotations,
    resolve_tool_output_schema,
)
from litestar_mcp.mcp.skill_controller import SkillController
from litestar_mcp.utils import (
    clean_icons,
    clean_meta,
    clean_title,
    get_handler_function,
    get_mcp_metadata,
    render_description,
    should_include_handler,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from litestar_mcp.core.context import ToolContext
    from litestar_mcp.mcp.config import MCPOptKeys

__all__ = (
    "agent_to_mcp",
    "discover_mcp_tools",
    "mcp_to_tools",
)


def agent_to_mcp(agent_or_group: Agent | AgentGroup, path: str = "/mcp") -> LitestarMCP:
    """Expose tools, prompts, and skills of an Agent or AgentGroup as a LitestarMCP plugin."""
    agents: list[Agent] = (
        [agent_or_group]
        if isinstance(agent_or_group, Agent)
        else [agent_or_group.coordinator, *agent_or_group.specialists.values()]
    )

    skills_list: list[type[Any]] = []
    seen_skills: set[type[Any]] = set()
    for ag in agents:
        for s in ag.skills:
            ctrl_type = s if isinstance(s, type) else type(s)
            if ctrl_type not in seen_skills:
                seen_skills.add(ctrl_type)
                skills_list.append(ctrl_type)

    config = MCPConfig(base_path=path, skill_controllers=skills_list)
    plugin = LitestarMCP(config=config)

    registered_names: set[str] = set()
    for ag in agents:
        for t in ag.tool_set:
            if t.name in registered_names:
                continue
            plugin.register_tool(t)
            registered_names.add(t.name)

    return plugin


def mcp_to_tools(
    client: MCPStreamableHTTPClient | LitestarMCP | Any,
    *,
    tools: Sequence[Any] | None = None,
    include_app_only: bool = False,
) -> list[Tool]:
    """Convert remote tools discovered from an MCP client or LitestarMCP plugin into callable Tool instances."""
    if isinstance(client, LitestarMCP):
        return _collect_litestar_mcp_tools(client, include_app_only=include_app_only)

    converted: list[Tool] = []
    raw_tools: Any = tools
    if raw_tools is None:
        raw_tools = getattr(client, "tools", [])
        if callable(raw_tools):
            raw_tools = raw_tools()

    if isinstance(raw_tools, Sequence) and not isinstance(raw_tools, (str, bytes)):
        for item in raw_tools:
            name, description, schema, annotations, output_schema, title, icons, meta = _extract_remote_tool_spec(item)
            if not _is_model_visible(meta, include_app_only=include_app_only):
                continue
            converted.append(
                Tool.from_schema(
                    name=name,
                    description=description,
                    parameters=schema,
                    call=_make_remote_call(client, name),
                    annotations=annotations,
                    output_schema=output_schema,
                    title=title,
                    icons=icons,
                    meta=meta,
                )
            )

    return converted


async def discover_mcp_tools(
    client_or_url: MCPStreamableHTTPClient | httpx2.AsyncClient | str,
    *,
    url: str = "/mcp",
    include_app_only: bool = False,
) -> list[Tool]:
    """Asynchronously discover tools from a remote MCP HTTP endpoint using httpx2 and wrap them as Tools."""
    if isinstance(client_or_url, MCPStreamableHTTPClient):
        client = client_or_url
    elif isinstance(client_or_url, httpx2.AsyncClient):
        client = MCPStreamableHTTPClient(endpoint=url, client=client_or_url)
    else:
        client = MCPStreamableHTTPClient(endpoint=client_or_url)

    raw_tools = await client.list_tools()
    return mcp_to_tools(client, tools=raw_tools, include_app_only=include_app_only)


def _make_remote_call(client: Any, target_name: str) -> Callable[..., Any]:
    """Build an async delegate invoking `client.call_tool` for a discovered remote tool."""

    async def _remote_call(**kwargs: Any) -> Any:
        if hasattr(client, "call_tool"):
            return await client.call_tool(target_name, kwargs)
        return f"Remote tool {target_name} called with {kwargs}"

    return _remote_call


def _is_model_visible(meta: Mapping[str, Any] | None, *, include_app_only: bool) -> bool:
    """Return whether a tool descriptor is visible to the model under SEP-1865 `_meta.ui.visibility`."""
    if include_app_only or not isinstance(meta, Mapping):
        return True
    ui: Any = meta.get("ui")
    if not isinstance(ui, Mapping) or "visibility" not in ui or ui.get("visibility") is None:
        return True
    visibility: Any = ui.get("visibility")
    if isinstance(visibility, str):
        return visibility == "model"
    if isinstance(visibility, Sequence) and not isinstance(visibility, bytes):
        return "model" in visibility
    return False


def _extract_tool_metadata(item: Any) -> tuple[str, str, dict[str, Any]]:
    """Extract tool name, description, and input schema from a dict or object."""
    if isinstance(item, Mapping):
        name = str(item.get("name", "tool"))
        raw_desc: Any = item.get("description")
        description = str(raw_desc) if raw_desc is not None else ""
        raw_schema: Any = item.get("inputSchema") or item.get("parameters")
        schema = (
            copy.deepcopy(dict(raw_schema)) if isinstance(raw_schema, Mapping) else {"type": "object", "properties": {}}
        )
        return name, description, schema
    name = str(getattr(item, "name", item))
    raw_obj_desc: Any = getattr(item, "description", None)
    description = str(raw_obj_desc) if raw_obj_desc is not None else ""
    raw_obj_schema: Any = getattr(item, "inputSchema", None) or getattr(item, "parameters", None)
    schema = (
        copy.deepcopy(dict(raw_obj_schema))
        if isinstance(raw_obj_schema, Mapping)
        else {"type": "object", "properties": {}}
    )
    return name, description, schema


def _extract_remote_tool_spec(
    item: Any,
) -> tuple[
    str,
    str,
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    str | None,
    list[dict[str, Any]] | None,
    dict[str, Any] | None,
]:
    """Extract full SEP-1865 and MCP tool descriptor fields from a remote dict or object."""
    name, description, schema = _extract_tool_metadata(item)
    if isinstance(item, Mapping):
        raw_annotations: Any = item.get("annotations")
        raw_output: Any = item.get("outputSchema") if "outputSchema" in item else item.get("output_schema")
        raw_title: Any = item.get("title")
        raw_icons: Any = item.get("icons")
        raw_meta: Any = item.get("_meta") if "_meta" in item else item.get("meta")
    else:
        raw_annotations = getattr(item, "annotations", None)
        raw_output = getattr(item, "outputSchema", None)
        if raw_output is None:
            raw_output = getattr(item, "output_schema", None)
        raw_title = getattr(item, "title", None)
        raw_icons = getattr(item, "icons", None)
        raw_meta = getattr(item, "_meta", None)
        if raw_meta is None:
            raw_meta = getattr(item, "meta", None)

    annotations = (
        copy.deepcopy(dict(raw_annotations)) if isinstance(raw_annotations, Mapping) and raw_annotations else None
    )
    output_schema = (
        unwrap_root_object_schema(copy.deepcopy(dict(raw_output)))
        if isinstance(raw_output, Mapping) and raw_output
        else None
    )
    return (
        name,
        description,
        schema,
        annotations,
        output_schema,
        clean_title(raw_title),
        clean_icons(raw_icons),
        clean_meta(raw_meta),
    )


def _is_tool_handler(handler: BaseRouteHandler, opt_keys: "MCPOptKeys") -> bool:
    """Return whether `handler` represents an MCP tool route rather than a resource or prompt."""
    raw_opt: Any = getattr(handler, "opt", None)
    opt: Mapping[str, Any] = raw_opt if isinstance(raw_opt, Mapping) else {}
    if bool(opt.get("_mcp_synthesized_tool")) or bool(opt.get(opt_keys.tool)):
        return True
    fn = get_handler_function(handler)
    meta = get_mcp_metadata(handler, kind="tool") or get_mcp_metadata(fn, kind="tool")
    return isinstance(meta, Mapping) and meta.get("type") == "tool"


def _resolve_dynamic_tool_name(handler: BaseRouteHandler, opt_keys: "MCPOptKeys") -> str | None:
    """Resolve the logical MCP tool name for a dynamic `BaseRouteHandler`."""
    raw_opt: Any = getattr(handler, "opt", None)
    opt: Mapping[str, Any] = raw_opt if isinstance(raw_opt, Mapping) else {}
    fn = get_handler_function(handler)
    opt_val: Any = opt.get(opt_keys.tool)
    resolved: str | None = None
    if opt_val is True:
        fn_name = getattr(fn, "__name__", None)
        resolved = fn_name.removeprefix("_tool_endpoint_") if isinstance(fn_name, str) and fn_name else None
    elif isinstance(opt_val, str) and opt_val:
        resolved = opt_val
    elif isinstance(opt_val, Mapping):
        mapped_name = opt_val.get("name")
        resolved = mapped_name if isinstance(mapped_name, str) and mapped_name else None

    if not resolved:
        meta = get_mcp_metadata(handler, kind="tool") or get_mcp_metadata(fn, kind="tool")
        meta_name = meta.get("name") if isinstance(meta, Mapping) else None
        resolved = meta_name if isinstance(meta_name, str) and meta_name else None
    if not resolved:
        default_opt = opt.get("mcp_tool")
        resolved = default_opt if isinstance(default_opt, str) and default_opt else None
    if not resolved:
        raw_name = getattr(fn, "__name__", None) or getattr(handler, "name", None)
        resolved = raw_name.removeprefix("_tool_endpoint_") if isinstance(raw_name, str) and raw_name else None
    return resolved or None


def _collect_skill_controller_entry(
    entry: type[SkillController] | SkillController,
    *,
    opt_keys: "MCPOptKeys",
    merged_handlers: dict[str, Any],
    skill_tools_by_name: dict[str, Tool],
) -> None:
    """Record bound skill `Tool` entries and tool-only route handlers from a `SkillController`."""
    ctrl: SkillController = entry() if isinstance(entry, type) else entry
    for skill_tool in ctrl.get_tools():
        skill_tools_by_name[skill_tool.name] = skill_tool
    for sub_handler in ctrl.get_route_handlers():
        if isinstance(sub_handler, BaseRouteHandler) and _is_tool_handler(sub_handler, opt_keys):
            sub_name = _resolve_dynamic_tool_name(sub_handler, opt_keys)
            if sub_name:
                merged_handlers[sub_name] = sub_handler


def _scan_local_tool_handlers(client: LitestarMCP) -> tuple[dict[str, Any], dict[str, Tool]]:
    """Collect tool-only dynamic handlers merged with app-discovered `LitestarMCP` tool handlers."""
    merged_handlers: dict[str, Any] = {}
    skill_tools_by_name: dict[str, Tool] = {}
    opt_keys = client.config.opt_keys
    dynamic_handlers: Any = getattr(client, "_dynamic_handlers", ())
    if isinstance(dynamic_handlers, Mapping):
        merged_handlers.update(dynamic_handlers)
    elif isinstance(dynamic_handlers, Sequence) and not isinstance(dynamic_handlers, (str, bytes)):
        for dyn_handler in dynamic_handlers:
            if isinstance(dyn_handler, Tool):
                merged_handlers[dyn_handler.name] = dyn_handler
                continue
            if (isinstance(dyn_handler, type) and issubclass(dyn_handler, SkillController)) or isinstance(
                dyn_handler, SkillController
            ):
                _collect_skill_controller_entry(
                    dyn_handler,
                    opt_keys=opt_keys,
                    merged_handlers=merged_handlers,
                    skill_tools_by_name=skill_tools_by_name,
                )
                continue
            if (isinstance(dyn_handler, type) and issubclass(dyn_handler, PromptController)) or isinstance(
                dyn_handler, PromptController
            ):
                continue
            if isinstance(dyn_handler, BaseRouteHandler) and _is_tool_handler(dyn_handler, opt_keys):
                dyn_name = _resolve_dynamic_tool_name(dyn_handler, opt_keys)
                if dyn_name:
                    merged_handlers[dyn_name] = dyn_handler
    merged_handlers.update(client.discovered_tools)
    return merged_handlers, skill_tools_by_name


def _resolve_owning_app(client: LitestarMCP, handlers: Sequence[Any]) -> Litestar | None:
    """Resolve the owning `Litestar` app from `client` or handler `ownership_layers`."""
    direct = getattr(client, "_app", None)
    if isinstance(direct, Litestar):
        return direct
    candidate_groups = (
        tuple(handlers),
        tuple(client.discovered_tools.values()),
        tuple(client.discovered_resources.values()),
    )
    for group in candidate_groups:
        for handler in group:
            for layer in getattr(handler, "ownership_layers", ()):
                if isinstance(layer, Litestar):
                    return layer
    return None


def _resolve_handler_tags(handler: Any) -> set[str]:
    """Resolve effective route tags including inherited router and controller tags."""
    resolve_tags = getattr(handler, "resolve_tags", None)
    if callable(resolve_tags):
        return set(cast("Iterable[str]", resolve_tags()))
    return set(cast("Iterable[str]", getattr(handler, "tags", None) or ()))


def _build_execution_request(app: Litestar, ctx: "ToolContext | None") -> Request[Any, Any, Any] | None:
    """Build a synthetic Litestar `Request` carrying `ToolContext` identity and request-scoped DI."""
    if ctx is None:
        return None
    if isinstance(ctx.request, Request):
        return ctx.request

    dishka = ctx.dishka_container
    non_app_dishka = (
        dishka
        if dishka is not None
        and getattr(getattr(getattr(dishka, "registry", None), "scope", None), "name", "") != "APP"
        else None
    )
    state: dict[str, Any] = dict(ctx.state) if ctx.state else {}
    if ctx.tenant_id is not None:
        state.setdefault("tenant_id", ctx.tenant_id)
    if non_app_dishka is not None:
        state["dishka_container"] = non_app_dishka

    if ctx.user is None and ctx.auth is None and ctx.session_id is None and not state:
        return None

    scope = blank_http_scope(app)
    if ctx.user is not None:
        scope["user"] = ctx.user
    if ctx.auth is not None:
        scope["auth"] = ctx.auth
    if ctx.session_id is not None:
        scope["session"] = {"session_id": ctx.session_id}
    if state:
        scope["state"] = state
    return Request(cast("Any", scope))


def _normalize_local_tool_result(result: Any, *, max_blob_bytes: int | None) -> Any:
    """Normalize `litestar_mcp.mcp.content` helpers returned by local Litestar route tools."""
    if isinstance(result, MCPInputRequiredResult):
        return result.to_result()
    if isinstance(result, (MCPBlobResource, MCPResourceLink)):
        return normalize_content_block(result, max_blob_bytes=max_blob_bytes)
    if isinstance(result, MCPToolResult):
        normalized = result.to_result(max_blob_bytes=max_blob_bytes)
        if normalized.get("isError"):
            raise MCPToolErrorResult(normalized)
        return normalized
    if isinstance(result, list) and any(isinstance(item, (MCPBlobResource, MCPResourceLink)) for item in result):
        return normalize_content_blocks(result, max_blob_bytes=max_blob_bytes)
    return result


def _make_local_call(
    handler: BaseRouteHandler,
    app: Litestar,
    client: LitestarMCP,
    tool_name: str,
) -> Callable[..., Any]:
    """Build an async delegate executing an app-bound route tool through `execute_tool`."""

    async def _local_call(**kwargs: Any) -> Any:
        request = _build_execution_request(app, current_tool_context())
        result = await execute_tool(
            handler,
            app,
            kwargs,
            request=request,
            config=client.config,
            tool_name=tool_name,
        )
        return _normalize_local_tool_result(result, max_blob_bytes=client.config.max_blob_bytes)

    return _local_call


def _convert_local_handler(
    tool_name: str,
    handler: Any,
    *,
    client: LitestarMCP,
    app: Litestar | None,
    skill_tools_by_name: Mapping[str, Tool],
    include_app_only: bool,
) -> Tool | None:
    """Convert one local `LitestarMCP` handler into a callable `Tool`, or `None` when filtered out."""
    if not should_include_handler(tool_name, _resolve_handler_tags(handler), client.config):
        return None
    if isinstance(handler, Tool):
        return handler if _is_model_visible(handler.meta, include_app_only=include_app_only) else None
    if not isinstance(handler, BaseRouteHandler):
        return None

    handler_fn = get_handler_function(handler)
    opt_keys = client.config.opt_keys
    metadata = get_mcp_metadata(handler, kind="tool") or get_mcp_metadata(handler_fn, kind="tool") or {}
    fallback_tool = skill_tools_by_name.get(tool_name)
    title, icons, meta = resolve_descriptor_fields(
        handler,
        kind="tool",
        opt_keys=opt_keys,
        metadata=metadata,
        fallback_source=fallback_tool,
    )
    if not _is_model_visible(meta, include_app_only=include_app_only):
        return None

    annotations = resolve_tool_annotations(handler, metadata, client.config)
    output_schema = resolve_tool_output_schema(handler, metadata, client.config)
    description = render_description(handler, handler_fn, kind="tool", fallback_name=tool_name, opt_keys=opt_keys)

    if app is not None:
        input_schema = (
            copy.deepcopy(metadata["input_schema"])
            if "input_schema" in metadata
            else copy.deepcopy(cached_schema_for_handler(handler))
        )
        return Tool.from_schema(
            name=tool_name,
            description=description,
            parameters=input_schema,
            call=_make_local_call(handler, app, client, tool_name),
            annotations=annotations or None,
            output_schema=output_schema,
            title=title,
            icons=icons,
            meta=meta,
        )

    return (
        fallback_tool
        if fallback_tool is not None
        else Tool(
            handler_fn,
            name=tool_name,
            title=title,
            description=description,
            dependencies=getattr(handler, "dependencies", None),
            output_schema=output_schema,
            annotations=annotations or None,
            icons=icons,
            meta=meta,
        )
    )


def _collect_litestar_mcp_tools(client: LitestarMCP, *, include_app_only: bool = False) -> list[Tool]:
    """Collect Tool instances from a LitestarMCP plugin before or after app initialization."""
    merged_handlers, skill_tools_by_name = _scan_local_tool_handlers(client)
    app = _resolve_owning_app(client, list(merged_handlers.values()))
    converted: list[Tool] = []
    for tool_name, handler in merged_handlers.items():
        built = _convert_local_handler(
            tool_name,
            handler,
            client=client,
            app=app,
            skill_tools_by_name=skill_tools_by_name,
            include_app_only=include_app_only,
        )
        if built is not None:
            converted.append(built)
    return converted
