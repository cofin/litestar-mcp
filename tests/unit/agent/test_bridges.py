"""Unit tests for agent protocol bridges."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx2
import pytest

pytest.importorskip("a2a")

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from litestar import Litestar

from litestar_mcp.a2a.plugin import LitestarA2A, _LitestarUser
from litestar_mcp.agent.bridges.a2a import _default_owner_resolver, agent_to_a2a
from litestar_mcp.agent.bridges.mcp import agent_to_mcp, discover_mcp_tools, mcp_to_tools
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest, TurnResponse
from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.plugin import LitestarMCP

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.events import EventQueue

    from litestar_mcp.core.context import ToolContext


@tool(description="Bridge test tool.")
def ping() -> str:
    """Return pong string."""
    return "pong"


def test_agent_to_mcp() -> None:
    """agent_to_mcp registers tools from an Agent into a LitestarMCP plugin."""
    agent = Agent(name="network_agent", tools=[ping])
    plugin = agent_to_mcp(agent, path="/mcp_bridge")
    assert isinstance(plugin, LitestarMCP)
    assert plugin.config.base_path == "/mcp_bridge"


def test_agent_to_mcp_group_excludes_transfer_tools() -> None:
    """agent_to_mcp registers specialist tools without synthetic transfer tools."""
    coord = Agent(name="coord", tools=[ping])
    spec = Agent(name="spec")
    group = AgentGroup(coordinator=coord, specialists=[spec])
    plugin = agent_to_mcp(group, path="/mcp_group")
    assert isinstance(plugin, LitestarMCP)


@pytest.mark.anyio
async def test_mcp_to_tools_from_client() -> None:
    """mcp_to_tools wraps client tools as Tool instances using from_schema."""

    class MockClient:
        def __init__(self) -> None:
            self.tools = [
                {
                    "name": "remote_echo",
                    "description": "Remote tool echo",
                    "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
                }
            ]

        async def call_tool(self, name: str, arguments: dict[str, str]) -> str:
            return f"echo: {arguments.get('msg', '')}"

    client = MockClient()
    tools = mcp_to_tools(client)
    assert len(tools) == 1
    assert tools[0].name == "remote_echo"
    assert tools[0].description == "Remote tool echo"

    res = await tools[0].execute({"msg": "hello"})
    assert res == "echo: hello"


@pytest.mark.anyio
async def test_discover_mcp_tools() -> None:
    """discover_mcp_tools fetches remote tools and converts them to Tools."""
    plugin = LitestarMCP()
    plugin.register_tool(ping)
    app = Litestar(plugins=[plugin])
    transport = httpx2.ASGITransport(app=cast("Any", app))
    client = MCPStreamableHTTPClient(endpoint="http://testserver/mcp", transport=transport)

    tools = await discover_mcp_tools(client)
    assert len(tools) >= 1
    tool_names = [t.name for t in tools]
    assert "ping" in tool_names


def test_agent_to_a2a_forwards_options_and_advertises_bindings() -> None:
    """agent_to_a2a forwards route options, auth, tenant_header, and enable_http_json."""
    agent = Agent(name="bridge_agent", description="Bridge A2A agent.", tools=[ping])
    plugin = agent_to_a2a(
        agent,
        base_url="http://testserver",
        path="/a2a/bridge",
        auth="bearer_policy",
        route_opt={"exclude_from_auth": False},
        agent_card_route_opt={"exclude_from_auth": True},
        tenant_header="X-A2A-Tenant",
        enable_http_json=True,
    )
    assert isinstance(plugin, LitestarA2A)
    assert plugin.config.auth == "bearer_policy"
    assert plugin.config.route_opt == {"exclude_from_auth": False}
    assert plugin.config.agent_card_route_opt == {"exclude_from_auth": True}
    assert plugin.config.tenant_header == "X-A2A-Tenant"
    assert plugin.config.enable_http_json is True
    bindings = [iface.protocol_binding for iface in plugin.agent_card.supported_interfaces]
    assert bindings == ["JSONRPC", "HTTP+JSON"]


@pytest.mark.parametrize(
    ("user", "tenant", "expected"),
    [
        (_LitestarUser(None), "", "anonymous"),
        (_LitestarUser(SimpleNamespace(username="alice")), "acme", "acme:alice"),
        ({"sub": "bob"}, "corp", "corp:bob"),
        ({"user_id": "u-1"}, "", "u-1"),
    ],
)
def test_default_owner_resolver_supports_litestar_user_and_mapping(
    user: Any,
    tenant: str,
    expected: str,
) -> None:
    """_default_owner_resolver resolves _LitestarUser and Mapping principals."""
    ctx = ServerCallContext(tenant=tenant)
    ctx.user = user
    assert _default_owner_resolver(ctx) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("user", "tenant", "expected_owner_key", "expected_tenant_id"),
    [
        (_LitestarUser(None), "", None, None),
        (_LitestarUser(SimpleNamespace(username="alice")), "acme", "acme:alice", "acme"),
        (_LitestarUser(SimpleNamespace(username="alice")), "", "default:alice", None),
    ],
)
async def test_bridge_agent_executor_tool_context_owner_alignment(
    user: Any,
    tenant: str,
    expected_owner_key: str | None,
    expected_tenant_id: str | None,
) -> None:
    """_BridgeAgentExecutor.execute decomposes owner_key without double-prefixing ToolContext."""
    captured_contexts: list[ToolContext | None] = []

    class RecordingRuntime(AgentRuntime):
        async def run_turn(self, request: TurnRequest) -> TurnResponse:
            captured_contexts.append(request.context)
            return cast("TurnResponse", SimpleNamespace(content="ok"))

    agent = Agent(name="bridge_agent", description="Bridge A2A agent.")
    plugin = agent_to_a2a(
        agent,
        base_url="http://testserver",
        runtime=RecordingRuntime(target=agent),
    )
    assert isinstance(plugin.request_handler, DefaultRequestHandler)
    executor = plugin.request_handler.agent_executor

    call_ctx = ServerCallContext(tenant=tenant)
    call_ctx.user = user
    req_ctx = SimpleNamespace(
        current_task=None,
        task_id="task-1",
        context_id="ctx-1",
        message=None,
        call_context=call_ctx,
        get_user_input=lambda: "hello",
    )
    async def _noop_enqueue(_event: Any) -> None:
        return None

    event_queue = cast("EventQueue", SimpleNamespace(enqueue_event=_noop_enqueue))
    await executor.execute(cast("RequestContext", req_ctx), event_queue)

    assert len(captured_contexts) == 1
    tool_ctx = captured_contexts[0]
    assert tool_ctx is not None
    assert tool_ctx.owner_key() == expected_owner_key
    assert tool_ctx.tenant_id == expected_tenant_id


def test_mcp_to_tools_includes_pre_init_dynamic_handlers() -> None:
    """mcp_to_tools extracts pre-init dynamic handlers via get_handler_function."""

    @tool(description="Lookup order by id.")
    def lookup_order(order_id: str) -> str:
        """Lookup order by id."""
        return f"order:{order_id}"

    plugin = LitestarMCP()
    plugin.register_tool(lookup_order)
    plugin.register_tool(ping)
    tools = mcp_to_tools(plugin)
    assert [t.name for t in tools] == ["lookup_order", "ping"]
