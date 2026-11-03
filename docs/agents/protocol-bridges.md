---
title: Protocol Bridges
---

# Protocol Bridges

`litestar_mcp.agent.bridges` provides four bidirectional adapters (`agent_to_mcp`, `mcp_to_tools`, `discover_mcp_tools`, and `agent_to_a2a`) that connect Litestar MCP route handlers, remote Streamable HTTP MCP servers, local `Agent` / `AgentGroup` / `AgentRuntime` instances, and official A2A 1.0 endpoints without duplicating tool schemas or handler logic.

--8<-- "tools/docs/includes/diagram-agent-runtime-and-bridges.html"

---

## Bridge Reference Matrix

| Bridge Function | Source | Target | Description |
| :--- | :--- | :--- | :--- |
| **`agent_to_mcp(agent_or_group, path="/mcp")`** | `Agent` or `AgentGroup` | `LitestarMCP` plugin | Exposes every unique `Tool` and `SkillController` attached to an `Agent` or `AgentGroup` as an MCP server endpoint while excluding synthetic `transfer_to_agent` handoff tools. |
| **`mcp_to_tools(client, *, tools=None)`** | `LitestarMCP` plugin or `MCPStreamableHTTPClient` | `list[Tool]` | Converts local discovered MCP route handlers (`client.discovered_tools`) or remote MCP tool schemas into callable `Tool` objects for an `Agent`. |
| **`await discover_mcp_tools(client_or_url, *, url="/mcp")`** | `MCPStreamableHTTPClient`, `httpx2.AsyncClient`, or URL `str` | `list[Tool]` | Queries `tools/list` over Streamable HTTP via `MCPStreamableHTTPClient` and wraps every remote tool as an async `Tool.from_schema(...)` callable. |
| **`agent_to_a2a(agent_or_group, path="/a2a", *, base_url=..., runtime=...)`** | `Agent` or `AgentGroup` (+ `AgentRuntime`) | `LitestarA2A` plugin | Synthesizes an A2A 1.0 `AgentCard` (converting attached `SkillController` instances via `to_agent_skill()`), wraps `AgentRuntime` in an official `a2a-sdk` `AgentExecutor`, and returns a configured `LitestarA2A` plugin. |

---

## Complete Example: Bridging MCP Routes, `AgentRuntime`, and A2A 1.0

The snippet below demonstrates all three bridge directions in a single self-contained Litestar application:

1. `mcp_to_tools(catalog_mcp)` converts the `@get("/inventory/{sku:str}", mcp_tool="lookup_inventory")` route handler into a `Tool` attached to `inventory_agent`.
2. `agent_to_mcp(inventory_agent, path="/mcp")` exposes both the bridged `lookup_inventory` tool and `InventorySkillController.check_reorder_threshold` over MCP at `/mcp`.
3. `agent_to_a2a(inventory_agent, path="/a2a", base_url="https://agents.example.com", runtime=runtime)` mounts the agent's `AgentRuntime` as an A2A 1.0 `AgentExecutor` serving `/.well-known/agent-card.json`, JSON-RPC 2.0 at `/a2a`, and `HTTP+JSON` REST routes.

```python title="docs/examples/snippets/protocol_bridges.py"
--8<-- "docs/examples/snippets/protocol_bridges.py"
```

---

## 1. Exposing Agents Over MCP (`agent_to_mcp`)

`agent_to_mcp(agent_or_group, path="/mcp")` inspects an `Agent` or `AgentGroup` and builds a `LitestarMCP` plugin:

- **Skill Controller Deduplication**: Collects all `SkillController` classes or instances from the agent (or from `group.coordinator` and `group.specialists.values()`) and passes them to `MCPConfig(base_path=path, skill_controllers=skills_list)`.
- **Tool Registration**: Registers each unique `Tool` in `ag.tool_set` via `plugin.register_tool(t)`. Because `agent_to_mcp` reads `ag.tool_set` rather than `group.tools_for(ag)`, internal `transfer_to_agent` handoff tools are never leaked to external MCP clients.

If you also want to expose a full multi-turn `AgentRuntime` execution as a single composite MCP tool callable via `tools/call`, wrap `runtime.run_turn` with `@tool`:

```python
from litestar_mcp import tool
from litestar_mcp.agent import AgentRuntime, TurnRequest
from litestar_mcp.core.context import ToolContext


def runtime_as_mcp_tool(runtime: AgentRuntime, *, name: str = "ask_agent", description: str = "Run an agent turn."):
    """Wrap an AgentRuntime as a single callable Tool for MCP clients."""

    @tool(name=name, description=description)
    async def _invoke(prompt: str, ctx: ToolContext, session_id: str | None = None) -> dict[str, str]:
        response = await runtime.run_turn(
            TurnRequest(user_message=prompt, session_id=session_id, context=ctx)
        )
        return {
            "session_id": response.session_id,
            "turn_id": response.turn_id,
            "agent_name": response.agent_name,
            "output": response.output,
        }

    return _invoke
```

---

## 2. Consuming Local & Remote MCP Tools (`mcp_to_tools` & `discover_mcp_tools`)

### Local `LitestarMCP` Route Handlers (`mcp_to_tools`)

When passed a `LitestarMCP` plugin instance after route discovery, `mcp_to_tools(plugin)` iterates over `plugin.discovered_tools` and wraps each handler's underlying callable in a `Tool(handler_fn, name=tool_name)`. This lets an in-process `Agent` invoke your existing `@get` / `@post` MCP-marked route handlers with full `ToolContext` and DI resolution.

### Remote Streamable HTTP Servers (`discover_mcp_tools`)

To equip an `Agent` with tools hosted on an external MCP server (or an in-process ASGI app tested over `httpx2.ASGITransport`), use `discover_mcp_tools` with `MCPStreamableHTTPClient` (`litestar_mcp.MCPStreamableHTTPClient`):

```python
import httpx2
from litestar_mcp import MCPStreamableHTTPClient
from litestar_mcp.agent import Agent, discover_mcp_tools


async def build_remote_equipped_agent(http_client: httpx2.AsyncClient) -> Agent:
    """Discover tools from a remote Streamable HTTP MCP endpoint and bind them to an Agent."""
    mcp_client = MCPStreamableHTTPClient(
        endpoint="https://mcp.internal.example.com/mcp",
        client=http_client,
    )
    remote_tools = await discover_mcp_tools(mcp_client)
    return Agent(
        name="ops_assistant",
        description="Executes operational queries against remote MCP services.",
        instructions="Use discovered remote tools to inspect service status.",
        tools=remote_tools,
    )
```

`discover_mcp_tools` invokes `await client.list_tools()` and delegates to `mcp_to_tools(client, tools=raw_tools)`, which constructs a `Tool.from_schema(name=..., description=..., parameters=..., call=...)` for each remote tool. When the LLM calls one of those tools during an `AgentRuntime` turn, the synthesized async `call` coroutine forwards the validated keyword arguments to `await client.call_tool(target_name, kwargs)`.

---

## 3. Adapting `AgentRuntime` to Official A2A 1.0 (`agent_to_a2a`)

`agent_to_a2a` (`litestar_mcp.agent.agent_to_a2a`, enabled via `litestar-mcp[a2a]`) bridges an `Agent` or `AgentGroup` and its `AgentRuntime` into an official `a2a-sdk` 1.0 `LitestarA2A` plugin:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `agent_or_group` | `Agent \| AgentGroup` | *(required)* | Target `Agent` or `AgentGroup` (uses `group.coordinator` for card metadata). |
| `path` | `str` | `"/a2a"` | Relative mount path (or absolute URL) for the A2A JSON-RPC and `HTTP+JSON` routes. |
| `base_url` | `str` | *(required)* | Public base URL combined with `path` to populate `AgentCard.supported_interfaces`. |
| `runtime` | `AgentRuntime \| None` | `None` | `AgentRuntime` executing incoming A2A requests. Defaults to `AgentRuntime(target=agent_or_group)`. |
| `task_store` | `TaskStore \| None` | `None` | Custom A2A `TaskStore`. Defaults to `InMemoryTaskStore(owner_resolver=...)`. |
| `owner_resolver` | `Callable[[ServerCallContext], str] \| None` | `None` | Resolves an owner key from `ServerCallContext` (defaults to `"{tenant}:{user_id}"`). |
| `security_schemes` / `security_requirements` | `dict` / `list` | `None` | Optional A2A security declarations included on the generated `AgentCard`. |
| `route_opt` / `agent_card_route_opt` | `dict[str, Any] \| None` | `None` | Litestar route `opt` dictionaries forwarded to `A2AConfig`. |
| `tenant_header` | `str \| None` | `"X-A2A-Tenant"` | HTTP fallback header for multi-tenant resolution. |
| `enable_http_json` | `bool` | `True` | Mount and advertise both `JSONRPC` and `HTTP+JSON` 1.0 interfaces. |

When an A2A client sends a message to the bridged endpoint:

1. `_BridgeAgentExecutor.execute` enqueues an initial `Task(status=TaskStatus(state=TaskState.TASK_STATE_WORKING))` event if `context.current_task is None`.
2. It resolves the caller's owner key via `owner_resolver(context.call_context)`, constructs a `ToolContext(user={"id": owner_key}, session_id=context.context_id, turn_id=context.task_id)`, and invokes `await runtime.run_turn(TurnRequest(...))`.
3. Because `session_id` is bound to `context.context_id`, multi-turn A2A conversations sharing a `context_id` automatically resume their owner-isolated transcript in `runtime.sessions`.
4. The executor emits a `TaskArtifactUpdateEvent(artifact=Artifact(artifact_id="response", parts=[Part(text=turn_res.content)]), last_chunk=True)` followed by `TaskStatusUpdateEvent(status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))`.
