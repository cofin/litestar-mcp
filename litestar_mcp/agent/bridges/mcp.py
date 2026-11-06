"""MCP protocol bridge for Agent and AgentGroup instances."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx2

from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.tools import Tool
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.utils import get_handler_function

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
) -> list[Tool]:
    """Convert remote tools discovered from an MCP client or LitestarMCP plugin into callable Tool instances."""
    if isinstance(client, LitestarMCP):
        return _collect_litestar_mcp_tools(client)

    converted: list[Tool] = []

    raw_tools = tools
    if raw_tools is None:
        raw_tools = getattr(client, "tools", [])
        if callable(raw_tools):
            raw_tools = raw_tools()

    if isinstance(raw_tools, (list, tuple)):
        for item in raw_tools:
            name, description, schema = _extract_tool_metadata(item)

            def _make_call(target_name: str) -> Callable[..., Any]:
                async def _remote_call(**kwargs: Any) -> Any:
                    if hasattr(client, "call_tool"):
                        return await client.call_tool(target_name, kwargs)
                    return f"Remote tool {target_name} called with {kwargs}"

                return _remote_call

            converted.append(
                Tool.from_schema(
                    name=name,
                    description=description,
                    parameters=schema,
                    call=_make_call(name),
                )
            )

    return converted


async def discover_mcp_tools(
    client_or_url: MCPStreamableHTTPClient | httpx2.AsyncClient | str,
    *,
    url: str = "/mcp",
) -> list[Tool]:
    """Asynchronously discover tools from a remote MCP HTTP endpoint using httpx2 and wrap them as Tools."""
    if isinstance(client_or_url, MCPStreamableHTTPClient):
        client = client_or_url
    elif isinstance(client_or_url, httpx2.AsyncClient):
        client = MCPStreamableHTTPClient(endpoint=url, client=client_or_url)
    else:
        client = MCPStreamableHTTPClient(endpoint=client_or_url)

    raw_tools = await client.list_tools()
    return mcp_to_tools(client, tools=raw_tools)


def _collect_litestar_mcp_tools(client: LitestarMCP) -> list[Tool]:
    """Collect Tool instances from a LitestarMCP plugin before or after app init."""
    converted: list[Tool] = []
    merged_handlers: dict[str, Any] = {}
    dynamic_handlers = getattr(client, "_dynamic_handlers", ())
    if isinstance(dynamic_handlers, Mapping):
        merged_handlers.update(dynamic_handlers)
    else:
        for dyn_handler in dynamic_handlers:
            opt = getattr(dyn_handler, "opt", None)
            opt_meta = opt.get("mcp_tool") if isinstance(opt, Mapping) else None
            dyn_fn = get_handler_function(dyn_handler)
            dyn_name = (
                opt_meta
                if isinstance(opt_meta, str) and opt_meta
                else (opt_meta.get("name") if isinstance(opt_meta, Mapping) else getattr(opt_meta, "name", None))
            )
            if not isinstance(dyn_name, str) or not dyn_name:
                raw_name = getattr(dyn_fn, "__name__", None) or getattr(dyn_handler, "name", None)
                dyn_name = raw_name.removeprefix("_tool_endpoint_") if isinstance(raw_name, str) else None
            if isinstance(dyn_name, str) and dyn_name:
                merged_handlers[dyn_name] = dyn_handler
    merged_handlers.update(client.discovered_tools)
    for tool_name, handler in merged_handlers.items():
        if isinstance(handler, Tool):
            converted.append(handler)
            continue
        handler_fn = get_handler_function(handler)
        if handler_fn is not None:
            converted.append(Tool(handler_fn, name=tool_name))
    return converted


def _extract_tool_metadata(item: Any) -> tuple[str, str, dict[str, Any]]:
    """Extract tool name, description, and input schema from a dict or object."""
    if isinstance(item, dict):
        name = str(item.get("name", "tool"))
        description = str(item.get("description", ""))
        schema = item.get("inputSchema") or item.get("parameters") or {"type": "object", "properties": {}}
        return name, description, dict(schema)
    name = str(getattr(item, "name", item))
    description = str(getattr(item, "description", ""))
    raw_schema = getattr(item, "inputSchema", None) or getattr(item, "parameters", None) or {}
    schema = dict(raw_schema) if isinstance(raw_schema, dict) else {"type": "object", "properties": {}}
    return name, description, schema
