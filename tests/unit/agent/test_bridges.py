"""Unit tests for agent protocol bridges."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx2
import msgspec
import pytest
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.litestar import FromDishka, inject, setup_dishka
from litestar import Litestar, Request, Router, get, post
from litestar.di import Provide
from litestar.exceptions import PermissionDeniedException
from litestar.testing import TestClient

from litestar_mcp import (
    MCP,
    MCPBlobResource,
    MCPConfig,
    MCPInputRequiredResult,
    MCPOptKeys,
    MCPResourceLink,
    MCPToolResult,
    PromptController,
    SkillController,
    mcp_prompt,
    mcp_tool,
    prompt,
)
from litestar_mcp.agent.bridges.mcp import agent_to_mcp, discover_mcp_tools, mcp_to_tools
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest, TurnResponse
from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.executor import MCPToolErrorResult
from litestar_mcp.mcp.plugin import LitestarMCP

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler

    from litestar_mcp.a2a.plugin import LitestarA2A, _LitestarUser
    from litestar_mcp.agent.bridges.a2a import _default_owner_resolver, agent_to_a2a

    _HAS_A2A = True
else:
    try:
        from a2a.server.context import ServerCallContext
        from a2a.server.request_handlers import DefaultRequestHandler

        from litestar_mcp.a2a.plugin import LitestarA2A, _LitestarUser
        from litestar_mcp.agent.bridges.a2a import _default_owner_resolver, agent_to_a2a

        _HAS_A2A = True
    except ImportError:
        _HAS_A2A = False


@dataclass
class _BridgeRepo:
    prefix: str


class _BridgeDishkaUnit:
    def __init__(self, instance_id: int) -> None:
        self.instance_id = instance_id
        self.closed = False


class _BridgeItem(msgspec.Struct, rename="camel"):
    item_id: str


def _make_a2a_principal(kind: str, ident: str | None) -> Any:
    if kind == "litestar_none":
        return _LitestarUser(None)
    if kind == "litestar_user":
        return _LitestarUser(SimpleNamespace(username=ident))
    if kind == "dict_sub":
        return {"sub": ident}
    return {"user_id": ident}


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


@pytest.mark.skipif(not _HAS_A2A, reason="a2a extra not installed")
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


@pytest.mark.skipif(not _HAS_A2A, reason="a2a extra not installed")
@pytest.mark.parametrize(
    ("user_kind", "user_ident", "tenant", "expected"),
    [
        ("litestar_none", None, "", "anonymous"),
        ("litestar_user", "alice", "acme", "acme:alice"),
        ("dict_sub", "bob", "corp", "corp:bob"),
        ("dict_uid", "u-1", "", "u-1"),
    ],
)
def test_default_owner_resolver_supports_litestar_user_and_mapping(
    user_kind: str,
    user_ident: str | None,
    tenant: str,
    expected: str,
) -> None:
    """_default_owner_resolver resolves _LitestarUser and Mapping principals."""
    ctx = ServerCallContext(tenant=tenant)
    ctx.user = _make_a2a_principal(user_kind, user_ident)
    assert _default_owner_resolver(ctx) == expected


@pytest.mark.skipif(not _HAS_A2A, reason="a2a extra not installed")
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("user_kind", "user_ident", "tenant", "expected_owner_key", "expected_tenant_id"),
    [
        ("litestar_none", None, "", None, None),
        ("litestar_user", "alice", "acme", "acme:alice", "acme"),
        ("litestar_user", "alice", "", "default:alice", None),
    ],
)
async def test_bridge_agent_executor_tool_context_owner_alignment(
    user_kind: str,
    user_ident: str | None,
    tenant: str,
    expected_owner_key: str | None,
    expected_tenant_id: str | None,
) -> None:
    """_BridgeAgentExecutor.execute decomposes owner_key without double-prefixing ToolContext."""
    captured_contexts: list[ToolContext | None] = []

    class RecordingRuntime(AgentRuntime):
        async def run_turn(self, turn: TurnRequest) -> TurnResponse:
            captured_contexts.append(turn.context)
            return cast("TurnResponse", SimpleNamespace(content="ok"))

    agent = Agent(name="bridge_agent", description="Bridge A2A agent.")
    plugin = agent_to_a2a(
        agent,
        base_url="http://testserver",
        runtime=RecordingRuntime(target=agent),
    )
    assert isinstance(plugin.request_handler, DefaultRequestHandler)
    executor = cast("Any", plugin.request_handler).agent_executor

    call_ctx = ServerCallContext(tenant=tenant)
    call_ctx.user = _make_a2a_principal(user_kind, user_ident)
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


@pytest.mark.anyio
@pytest.mark.parametrize("shape", ["dict", "object"])
@pytest.mark.parametrize(
    ("meta", "include_app_only", "expect_included"),
    [
        (None, False, True),
        ({"ui": {}}, False, True),
        ({"ui": {"visibility": ["app"]}}, False, False),
        ({"ui": {"visibility": ["app"]}}, True, True),
        ({"ui": {"visibility": "app"}}, False, False),
        ({"ui": {"visibility": "app"}}, True, True),
        ({"ui": {"visibility": []}}, False, False),
        ({"ui": {"visibility": []}}, True, True),
        ({"ui": {"visibility": ["model"]}}, False, True),
        ({"ui": {"visibility": "model"}}, False, True),
        ({"ui": {"visibility": ["model", "app"]}}, False, True),
    ],
)
async def test_mcp_to_tools_remote_visibility_and_descriptor_preservation(
    shape: str,
    meta: dict[str, Any] | None,
    include_app_only: bool,
    expect_included: bool,
) -> None:
    input_schema = {"type": "object", "properties": {"query_text": {"type": "string"}}}
    wrapped_output_schema = {
        "$ref": "#/$defs/Out",
        "$defs": {"Out": {"type": "object", "properties": {"ok": {"type": "boolean"}}}},
    }
    icons = [{"src": "https://example.com/remote.svg"}]
    annotations = {"readOnlyHint": True}
    if shape == "dict":
        spec: Any = {
            "name": "remote_tool",
            "description": "Remote tool",
            "inputSchema": input_schema,
            "outputSchema": wrapped_output_schema,
            "annotations": annotations,
            "title": "  Remote Title  ",
            "icons": icons,
            "_meta": meta,
        }
    else:
        spec = SimpleNamespace(
            name="remote_tool",
            description="Remote tool",
            parameters=input_schema,
            output_schema=wrapped_output_schema,
            annotations=annotations,
            title="  Remote Title  ",
            icons=icons,
            meta=meta,
        )

    class RemoteClient:
        def __init__(self, item: Any) -> None:
            self.tools = [item]

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return {"tool": name, "echo": arguments["query_text"]}

    converted = mcp_to_tools(RemoteClient(spec), include_app_only=include_app_only)
    if not expect_included:
        assert converted == []
        return

    assert len(converted) == 1
    remote = converted[0]
    assert remote.title == "Remote Title"
    assert remote.icons == icons
    assert remote.annotations == annotations
    assert remote.output_schema == {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    assert remote.meta == (meta or None)
    assert await remote.execute({"query_text": "hi"}) == {"tool": "remote_tool", "echo": "hi"}


@pytest.mark.anyio
@pytest.mark.parametrize("include_app_only", [False, True])
async def test_discover_mcp_tools_enforces_visibility_and_preserves_metadata(
    include_app_only: bool,
) -> None:
    icons = [{"src": "https://example.com/visible.svg"}]

    @mcp_tool(
        "visible_tool",
        title="Visible Tool",
        icons=icons,
        annotations={"idempotentHint": True},
        output_schema=True,
        meta={"ui": {"resourceUri": "ui://demo/view.html", "visibility": ["model", "app"]}},
    )
    @get("/visible", sync_to_thread=False)
    def visible_tool() -> _BridgeItem:
        return _BridgeItem(item_id="v-1")

    @mcp_tool(
        "app_refresh",
        title="App Refresh",
        output_schema=True,
        meta={"ui": {"visibility": ["app"]}},
    )
    @post("/refresh", sync_to_thread=False)
    def app_refresh() -> _BridgeItem:
        return _BridgeItem(item_id="r-1")

    app = Litestar(route_handlers=[visible_tool, app_refresh], plugins=[LitestarMCP()])
    transport = httpx2.ASGITransport(app=cast("Any", app))
    client = MCPStreamableHTTPClient(endpoint="http://testserver/mcp", transport=transport)

    discovered = {t.name: t for t in await discover_mcp_tools(client, include_app_only=include_app_only)}
    assert "visible_tool" in discovered
    vis = discovered["visible_tool"]
    assert vis.title == "Visible Tool"
    assert vis.icons == icons
    assert vis.annotations is not None
    assert vis.annotations["readOnlyHint"] is True
    assert vis.annotations["idempotentHint"] is True
    assert vis.output_schema is not None
    assert "itemId" in vis.output_schema["properties"]
    assert vis.meta == {"ui": {"resourceUri": "ui://demo/view.html", "visibility": ["model", "app"]}}

    if include_app_only:
        assert "app_refresh" in discovered
        assert discovered["app_refresh"].title == "App Refresh"
        assert discovered["app_refresh"].meta == {"ui": {"visibility": ["app"]}}
    else:
        assert "app_refresh" not in discovered


class _BridgePrompts(PromptController):
    path = "/bridge-prompts"
    prefix = "bp"

    @prompt(name="triage", title="Triage")
    async def triage(self) -> str:
        return "Triage steps"


class _BridgeSkills(SkillController):
    path = "/bridge-skills"
    prefix = "bs"

    @tool(name="skill_vis", title="Skill Visible", meta={"ui": {"visibility": ["model", "app"]}})
    def skill_vis(self) -> dict[str, str]:
        return {"skill": "ok"}

    @tool(name="skill_app", title="Skill App", meta={"ui": {"visibility": ["app"]}})
    def skill_app(self) -> dict[str, str]:
        return {"skill": "app"}

    @prompt(name="skill_guide", title="Skill Guide")
    async def skill_guide(self) -> str:
        return "Skill prompt"


@pytest.mark.parametrize("include_app_only", [False, True])
def test_mcp_to_tools_filters_non_tools_visibility_and_inherited_router_tags(
    include_app_only: bool,
) -> None:
    @get("/secret", mcp_tool="secret_admin", sync_to_thread=False)
    def secret_admin() -> dict[str, str]:
        return {"secret": "1"}

    @get("/blocked", mcp_tool="blocked_tool", sync_to_thread=False)
    def blocked_tool() -> dict[str, str]:
        return {"blocked": "1"}

    @get("/res-only", mcp_resource="cfg_res", mcp_resource_uri="litestar://cfg", sync_to_thread=False)
    def cfg_res() -> dict[str, str]:
        return {"cfg": "1"}

    @get("/prompt-only", mcp_prompt="route_prompt", sync_to_thread=False)
    def route_prompt() -> dict[str, Any]:
        return {"messages": []}

    @mcp_prompt(name="fn_prompt")
    def fn_prompt() -> str:
        return "hi"

    @get(
        "/public",
        mcp_tool="public_tool",
        mcp_tool_title="Public Tool",
        mcp_tool_meta={"ui": {"visibility": ["model", "app"]}},
        sync_to_thread=False,
    )
    def public_tool() -> dict[str, str]:
        return {"ok": "1"}

    @get(
        "/ui-poll",
        mcp_tool="ui_poll",
        mcp_tool_title="UI Poll",
        mcp_tool_meta={"ui": {"visibility": ["app"]}},
        sync_to_thread=False,
    )
    def ui_poll() -> dict[str, str]:
        return {"poll": "1"}

    admin_router = Router(path="/admin", tags=["internal"], route_handlers=[secret_admin])
    plugin = LitestarMCP(
        config=MCPConfig(
            prompt_controllers=[_BridgePrompts],
            skill_controllers=[_BridgeSkills],
            exclude_tags=["internal"],
            exclude_operations=["blocked_tool"],
        ),
        prompts=[fn_prompt],
    )
    Litestar(
        route_handlers=[admin_router, blocked_tool, cfg_res, route_prompt, public_tool, ui_poll],
        plugins=[plugin],
    )

    names = {t.name for t in mcp_to_tools(plugin, include_app_only=include_app_only)}
    expected = (
        {"public_tool", "skill_vis", "ui_poll", "skill_app"} if include_app_only else {"public_tool", "skill_vis"}
    )
    assert names == expected


@pytest.mark.parametrize("include_app_only", [False, True])
def test_mcp_to_tools_standalone_and_pre_init_exclude_prompts_and_resources(
    include_app_only: bool,
) -> None:
    mcp_srv = MCP(name="bridge-standalone")

    @mcp_srv.tool(name="s_vis", title="Standalone Vis", sync_to_thread=False)
    def s_vis() -> str:
        return "ok"

    @mcp_srv.tool(name="s_app", meta={"ui": {"visibility": ["app"]}}, sync_to_thread=False)
    def s_app() -> str:
        return "app"

    @mcp_srv.resource("ui://s/panel.html", name="s_res", sync_to_thread=False)
    def s_res() -> str:
        return "<main>UI</main>"

    @mcp_srv.prompt(name="s_prompt", sync_to_thread=False)
    def s_prompt() -> str:
        return "prompt"

    pre_init_names = {t.name for t in mcp_to_tools(mcp_srv.plugin, include_app_only=include_app_only)}
    _ = mcp_srv.app
    post_init_names = {t.name for t in mcp_to_tools(mcp_srv.plugin, include_app_only=include_app_only)}

    pre_ctrl = LitestarMCP(MCPConfig(prompt_controllers=[_BridgePrompts], skill_controllers=[_BridgeSkills]))
    ctrl_names = {t.name for t in mcp_to_tools(pre_ctrl, include_app_only=include_app_only)}

    expected_standalone = {"s_vis", "s_app"} if include_app_only else {"s_vis"}
    expected_ctrl = {"skill_vis", "skill_app"} if include_app_only else {"skill_vis"}
    assert pre_init_names == expected_standalone
    assert post_init_names == expected_standalone
    assert ctrl_names == expected_ctrl


def test_mcp_to_tools_preserves_local_annotations_output_schema_and_renamed_opt_keys() -> None:
    @get("/read-item", mcp_tool="read_item", sync_to_thread=False)
    def read_item() -> _BridgeItem:
        return _BridgeItem(item_id="1")

    @post(
        "/write-item",
        mcp_tool="write_item",
        mcp_annotations={"destructiveHint": False, "idempotentHint": False},
        sync_to_thread=False,
    )
    def write_item() -> _BridgeItem:
        return _BridgeItem(item_id="2")

    @mcp_tool("scoped_item", title="Scoped Item", scopes=["items:read"], output_schema=False)
    @get("/scoped-item", sync_to_thread=False)
    def scoped_item() -> _BridgeItem:
        return _BridgeItem(item_id="3")

    plugin = LitestarMCP(MCPConfig(auto_output_schema=True))
    Litestar(route_handlers=[read_item, write_item, scoped_item], plugins=[plugin])
    by_name = {t.name: t for t in mcp_to_tools(plugin)}

    assert by_name["read_item"].annotations == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    }
    assert by_name["read_item"].output_schema is not None
    assert "itemId" in by_name["read_item"].output_schema["properties"]

    assert by_name["write_item"].annotations == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
    }
    assert by_name["write_item"].output_schema is not None

    assert by_name["scoped_item"].title == "Scoped Item"
    assert by_name["scoped_item"].output_schema is None
    assert by_name["scoped_item"].annotations is not None
    assert by_name["scoped_item"].annotations["scopes"] == ["items:read"]

    opt_keys = MCPOptKeys(
        tool="x_t",
        tool_title="x_tt",
        tool_meta="x_tm",
        annotations="x_ann",
        read_only="x_ro",
        output_schema="x_os",
    )

    @post(
        "/renamed-op",
        opt={
            "x_t": "custom_op",
            "x_tt": "Custom Op",
            "x_tm": {"ui": {"visibility": ["app"]}},
            "x_ro": True,
            "x_ann": {"idempotentHint": True},
            "x_os": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        },
        sync_to_thread=False,
    )
    def custom_op() -> dict[str, bool]:
        return {"ok": True}

    renamed_plugin = LitestarMCP(MCPConfig(opt_keys=opt_keys))
    Litestar(route_handlers=[custom_op], plugins=[renamed_plugin])

    assert mcp_to_tools(renamed_plugin) == []
    included = mcp_to_tools(renamed_plugin, include_app_only=True)
    assert len(included) == 1
    assert included[0].title == "Custom Op"
    assert included[0].meta == {"ui": {"visibility": ["app"]}}
    assert included[0].annotations == {"readOnlyHint": True, "idempotentHint": True}
    assert included[0].output_schema == {"type": "object", "properties": {"ok": {"type": "boolean"}}}


@pytest.mark.anyio
async def test_mcp_to_tools_local_route_executes_provide_di_and_propagates_tool_context() -> None:
    def provide_repo() -> _BridgeRepo:
        return _BridgeRepo(prefix="ord")

    @get(
        "/items/{item_id:int}",
        mcp_tool="get_item",
        dependencies={"repo": Provide(provide_repo, sync_to_thread=False)},
        sync_to_thread=False,
    )
    def get_item(item_id: int, repo: _BridgeRepo, request: Request[Any, Any, Any]) -> dict[str, Any]:
        assert isinstance(request, Request)
        return {
            "key": f"{repo.prefix}:{item_id}",
            "user": request.user,
            "auth": request.auth,
            "session": request.session,
            "tenant": request.state.tenant_id,
        }

    plugin = LitestarMCP()
    app = Litestar(route_handlers=[get_item], plugins=[plugin])

    pre_startup_tool = mcp_to_tools(plugin)[0]
    assert set(pre_startup_tool.parameters["properties"]) == {"item_id"}

    ctx = ToolContext(user={"id": "u1"}, auth={"token": "tok"}, session_id="sess-9", tenant_id="acme")
    pre_res = await pre_startup_tool.execute({"item_id": 7}, context=ctx)
    assert pre_res == {
        "key": "ord:7",
        "user": {"id": "u1"},
        "auth": {"token": "tok"},
        "session": {"session_id": "sess-9"},
        "tenant": "acme",
    }

    with TestClient(app=app):
        post_startup_tool = mcp_to_tools(plugin)[0]
        post_res = await post_startup_tool.execute({"item_id": 8}, context=ctx)

    assert post_res["key"] == "ord:8"
    assert post_res["tenant"] == "acme"


@pytest.mark.anyio
async def test_mcp_to_tools_local_route_executes_dishka_request_scope_per_call() -> None:
    created: list[_BridgeDishkaUnit] = []

    class UnitProvider(Provider):
        @provide(scope=Scope.REQUEST)
        async def get_unit(self) -> AsyncIterator[_BridgeDishkaUnit]:
            unit = _BridgeDishkaUnit(instance_id=len(created) + 1)
            created.append(unit)
            try:
                yield unit
            finally:
                unit.closed = True

    @get("/dishka-op", mcp_tool="dishka_op")
    @inject
    async def dishka_op(label: str, unit: FromDishka[_BridgeDishkaUnit]) -> dict[str, Any]:
        return {"label": label, "instance": unit.instance_id, "closed_inside": unit.closed}

    container = make_async_container(UnitProvider())
    plugin = LitestarMCP()
    app = Litestar(route_handlers=[dishka_op], plugins=[plugin])
    setup_dishka(container, app)
    try:
        dishka_tool = mcp_to_tools(plugin)[0]
        assert set(dishka_tool.parameters["properties"]) == {"label"}

        first = await dishka_tool.execute({"label": "a"}, context=ToolContext())
        second = await dishka_tool.execute(
            {"label": "b"},
            context=ToolContext(dishka_container=container, tenant_id="t1"),
        )
    finally:
        await container.close()

    assert first == {"label": "a", "instance": 1, "closed_inside": False}
    assert second == {"label": "b", "instance": 2, "closed_inside": False}
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("return_value", "expected"),
    [
        (
            MCPInputRequiredResult(input_requests={"pin": {"prompt": "PIN"}}, request_state="st-1"),
            {"resultType": "input_required", "inputRequests": {"pin": {"prompt": "PIN"}}, "requestState": "st-1"},
        ),
        (
            MCPBlobResource(uri="app://bin/item", data=b"\x01\x02"),
            {
                "type": "resource",
                "resource": {"uri": "app://bin/item", "mimeType": "application/octet-stream", "blob": "AQI="},
            },
        ),
        (
            MCPResourceLink(name="doc", uri="app://doc", mime_type="text/markdown"),
            {"type": "resource_link", "name": "doc", "uri": "app://doc", "mimeType": "text/markdown"},
        ),
        (
            [MCPResourceLink(name="doc", uri="app://doc"), MCPBlobResource(uri="app://b", data=b"\x01")],
            [
                {"type": "resource_link", "name": "doc", "uri": "app://doc"},
                {
                    "type": "resource",
                    "resource": {"uri": "app://b", "mimeType": "application/octet-stream", "blob": "AQ=="},
                },
            ],
        ),
        (
            MCPToolResult(content="done", structured_content={"n": 1}),
            {
                "content": [{"type": "text", "text": "done"}],
                "structuredContent": {"n": 1},
                "isError": False,
            },
        ),
    ],
    ids=["input_required", "blob_resource", "resource_link", "content_list", "tool_result"],
)
async def test_mcp_to_tools_local_call_normalizes_mcp_content_helpers(
    return_value: Any,
    expected: Any,
) -> None:
    @get("/emit", mcp_tool="emit_content", sync_to_thread=False)
    def emit_content() -> Any:
        return return_value

    plugin = LitestarMCP(MCPConfig(max_blob_bytes=4))
    Litestar(route_handlers=[emit_content], plugins=[plugin])
    bridged = mcp_to_tools(plugin)[0]
    assert await bridged.execute({}) == expected


@pytest.mark.anyio
async def test_mcp_to_tools_local_call_enforces_blob_limit_and_raises_tool_errors() -> None:
    def deny_guard(_connection: Any, _handler: Any) -> None:
        raise PermissionDeniedException

    @get("/oversize", mcp_tool="oversize_blob", sync_to_thread=False)
    def oversize_blob() -> MCPBlobResource:
        return MCPBlobResource(uri="app://big", data=b"12345")

    @get("/err-res", mcp_tool="err_result", sync_to_thread=False)
    def err_result() -> MCPToolResult:
        return MCPToolResult(content="boom", is_error=True)

    @get("/guarded", mcp_tool="guarded_tool", guards=[deny_guard], sync_to_thread=False)
    def guarded_tool() -> dict[str, str]:
        return {"ok": "never"}

    plugin = LitestarMCP(MCPConfig(max_blob_bytes=4))
    Litestar(route_handlers=[oversize_blob, err_result, guarded_tool], plugins=[plugin])
    tools = {t.name: t for t in mcp_to_tools(plugin)}

    with pytest.raises(ValueError, match="max_blob_bytes"):
        await tools["oversize_blob"].execute({})

    with pytest.raises(MCPToolErrorResult) as err_info:
        await tools["err_result"].execute({})
    assert err_info.value.content["isError"] is True
    assert err_info.value.content["content"][0]["text"] == "boom"

    with pytest.raises(PermissionDeniedException):
        await tools["guarded_tool"].execute({})
