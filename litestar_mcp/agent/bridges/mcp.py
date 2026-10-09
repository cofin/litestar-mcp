"""MCP protocol bridge and httpx2 Streamable HTTP client for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx2

from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.agent.tools import Tool, tool
from litestar_mcp.core.serialization import from_json, to_json
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.skill_controller import build_tool_route_handler

if TYPE_CHECKING:
    from collections.abc import Callable


class MCPHttpClient:
    """Streamable HTTP MCP client powered by httpx2 and httpx2.EventSource."""

    def __init__(
        self,
        url: str,
        *,
        client: httpx2.AsyncClient | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self._client = client
        self.headers: dict[str, str] = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        self.session_id: str | None = None
        self.tools: list[dict[str, Any]] = []

    async def _send_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request over HTTP and decode JSON or SSE response."""
        rpc_id = uuid4().hex
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        req_headers = dict(self.headers)
        if self.session_id is not None:
            req_headers["Mcp-Session-Id"] = self.session_id

        body = to_json(payload, as_bytes=True)

        if self._client is not None:
            response = await self._client.post(self.url, content=body, headers=req_headers)
        else:
            async with httpx2.AsyncClient() as temp_client:
                response = await temp_client.post(self.url, content=body, headers=req_headers)

        response.raise_for_status()
        returned_session = response.headers.get("mcp-session-id")
        if returned_session:
            self.session_id = returned_session

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            event_source = httpx2.EventSource(response)
            async for sse in event_source:
                if not sse.data:
                    continue
                decoded = from_json(sse.data)
                if isinstance(decoded, dict) and ("result" in decoded or "error" in decoded):
                    return decoded
            return {}

        decoded_json = from_json(response.content)
        return decoded_json if isinstance(decoded_json, dict) else {"result": decoded_json}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Discover remote tools via JSON-RPC tools/list and cache definitions."""
        rpc_resp = await self._send_rpc("tools/list")
        result = rpc_resp.get("result", {})
        raw_tools = result.get("tools", []) if isinstance(result, dict) else []
        self.tools = [dict(t) for t in raw_tools if isinstance(t, dict)]
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a remote MCP tool via JSON-RPC tools/call."""
        rpc_resp = await self._send_rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if "error" in rpc_resp:
            err = rpc_resp["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(msg)
        result = rpc_resp.get("result", {})
        if isinstance(result, dict) and "structuredContent" in result:
            return result["structuredContent"]
        if isinstance(result, dict) and "content" in result:
            content_list = result["content"]
            if isinstance(content_list, list) and len(content_list) == 1:
                first = content_list[0]
                if isinstance(first, dict) and first.get("type") == "text":
                    return first.get("text", "")
            return content_list
        return result


def agent_to_mcp(agent_or_group: Agent | AgentGroup, path: str = "/mcp") -> LitestarMCP:
    """Expose tools, prompts, and skills of an Agent or AgentGroup as a LitestarMCP plugin."""
    agents: list[Agent] = (
        [agent_or_group]
        if isinstance(agent_or_group, Agent)
        else [agent_or_group.coordinator, *agent_or_group.specialists.values()]
    )

    skills_list: list[Any] = []
    seen_skills: set[int] = set()
    for ag in agents:
        for s in ag.skills:
            if id(s) not in seen_skills:
                seen_skills.add(id(s))
                skills_list.append(s)

    config = MCPConfig(base_path=path, skill_controllers=skills_list)
    plugin = LitestarMCP(config=config)

    registered_names = set(plugin.registry.tools.keys())
    for ag in agents:
        for t in ag.get_all_tools():
            if t.name in registered_names:
                continue
            tool_name, route_handler = build_tool_route_handler(
                t,
                base_path=path,
            )
            plugin.register_dynamic_handler(route_handler)
            plugin.registry.register_tool(tool_name, route_handler)
            registered_names.add(tool_name)

    return plugin


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


def mcp_to_tools(mcp_client: Any) -> list[Tool]:
    """Convert remote tools discovered from an MCP client or LitestarMCP plugin into callable Tool instances."""
    converted: list[Tool] = []

    if isinstance(mcp_client, LitestarMCP):
        for tool_name, handler in mcp_client.registry.tools.items():
            converted.append(tool(name=tool_name)(handler.fn))
        return converted

    tools_list = getattr(mcp_client, "tools", [])
    if callable(tools_list):
        tools_list = tools_list()

    if isinstance(tools_list, (list, tuple)):
        for item in tools_list:
            name, description, schema = _extract_tool_metadata(item)

            def make_invoker(target_name: str) -> Callable[..., Any]:
                async def remote_call(**kwargs: Any) -> Any:
                    if hasattr(mcp_client, "call_tool"):
                        return await mcp_client.call_tool(target_name, kwargs)
                    return f"Remote tool {target_name} called with {kwargs}"

                return remote_call

            callable_fn = make_invoker(name)
            callable_fn.__name__ = name
            callable_fn.__doc__ = description

            instance = Tool(
                name=name,
                description=description,
                fn=callable_fn,
                parameters=schema,
                stripped_parameters=set(),
            )
            converted.append(instance)

    return converted


async def discover_mcp_tools(
    client_or_url: MCPHttpClient | httpx2.AsyncClient | str,
    *,
    url: str = "/mcp",
) -> list[Tool]:
    """Asynchronously discover tools from a remote MCP HTTP endpoint using httpx2 and wrap them as Tools."""
    if isinstance(client_or_url, MCPHttpClient):
        http_mcp = client_or_url
    elif isinstance(client_or_url, httpx2.AsyncClient):
        http_mcp = MCPHttpClient(url=url, client=client_or_url)
    else:
        http_mcp = MCPHttpClient(url=client_or_url)

    await http_mcp.list_tools()
    return mcp_to_tools(http_mcp)


__all__ = (
    "MCPHttpClient",
    "agent_to_mcp",
    "discover_mcp_tools",
    "mcp_to_tools",
)
