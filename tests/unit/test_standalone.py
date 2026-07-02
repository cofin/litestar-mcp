import inspect
import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from litestar.config.app import AppConfig

from litestar import Litestar, get
from litestar.datastructures import ResponseHeader
from litestar.di import Provide
from litestar.openapi.datastructures import ResponseSpec
from litestar.plugins import InitPluginProtocol
from litestar.testing import TestClient

from litestar_mcp import MCP
from litestar_mcp.config import MCPConfig
from litestar_mcp.plugin import LitestarMCP


def _rpc(
    client: "TestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
    *,
    base_path: "str" = "/mcp",
) -> "dict[str, Any]":
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = getattr(client, "_mcp_session", "")
        if not sid:
            init = _rpc(client, "initialize", base_path=base_path)
            sid = init.get("_session_id", "")
            if not sid:
                sid = getattr(client, "_mcp_session", "")
        if sid:
            headers["Mcp-Session-Id"] = str(sid)
    response = client.post(base_path, json=body, headers=headers)
    result = response.json()
    sid = response.headers.get("mcp-session-id")
    if method == "initialize" and sid:
        client.post(
            base_path,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": sid},
        )
        client._mcp_session = sid  # type: ignore[attr-defined]
        result["_session_id"] = sid
    return result  # type: ignore[no-any-return]


def test_mcp_init_defaults() -> "None":
    mcp = MCP(name="test-mcp", instructions="test instructions")
    assert mcp.config.name == "test-mcp"
    assert mcp.config.instructions == "test instructions"
    assert isinstance(mcp.plugin, LitestarMCP)
    assert mcp.plugin in mcp._plugins


def test_mcp_init_custom_config() -> "None":
    config = MCPConfig(base_path="/custom")
    mcp = MCP(name="test-mcp", config=config)
    assert mcp.config.base_path == "/custom"
    assert mcp.config.name == "test-mcp"


def test_mcp_init_uses_plugin() -> "None":
    plugin = LitestarMCP()
    mcp = MCP(name="test-mcp", plugins=[plugin])
    assert mcp.plugin is plugin
    mcp_plugins = [p for p in mcp._plugins if isinstance(p, LitestarMCP)]
    assert len(mcp_plugins) == 1


def test_mcp_init_synchronizes_existing_plugin_metadata() -> "None":
    plugin = LitestarMCP()
    mcp = MCP(name="existing-plugin", instructions="Follow these instructions", plugins=[plugin])

    assert mcp.plugin is plugin
    assert mcp.config is plugin.config
    assert plugin.config.name == "existing-plugin"
    assert plugin.config.instructions == "Follow these instructions"

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "initialize")

    assert response["result"]["serverInfo"]["name"] == "existing-plugin"
    assert response["result"]["instructions"] == "Follow these instructions"


def test_mcp_lazy_app() -> "None":
    mcp = MCP(name="test-mcp")
    is_none = mcp._app is None
    assert is_none
    app = mcp.app
    assert isinstance(app, Litestar)
    assert mcp._app is app
    assert mcp.app is app


def test_mcp_decorators_registration() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="my_tool", description="tool desc")
    def tool_fn(x: "int") -> "int":
        """Doc desc"""
        return x + 1

    @mcp.resource(uri="app://my_resource", name="res_name", description="res desc")
    def resource_fn() -> "str":
        return "data"

    @mcp.prompt(name="my_prompt", description="prompt desc")
    def prompt_fn() -> "list[dict[str, Any]]":
        return []

    assert len(mcp.plugin._dynamic_handlers) == 3

    # Access app to trigger on_app_init on plugin
    _ = mcp.app

    registry = mcp.plugin._registry
    assert "my_tool" in registry.tools
    assert "res_name" in registry.resources
    assert "my_prompt" in registry.prompts

    tool_handler = registry.tools["my_tool"]
    assert tool_handler.opt["mcp_description"] == "tool desc"

    res_handler = registry.resources["res_name"]
    assert res_handler.opt["mcp_resource_description"] == "res desc"
    assert res_handler.opt["mcp_resource_template"] == "app://my_resource"

    prompt_reg = registry.prompts["my_prompt"]
    assert prompt_reg.description == "prompt desc"


def test_standalone_internal_tool_route_rejects_direct_http() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="echo", description="Echo")
    def echo(message: "str") -> "str":
        return message

    with TestClient(app=mcp.app) as client:
        direct = client.post("/mcp/internal/tools/echo", json={"message": "hello"})
        via_mcp = _rpc(client, "tools/call", {"name": "echo", "arguments": {"message": "hello"}})

    assert direct.status_code == 403
    assert via_mcp["result"]["isError"] is False
    assert via_mcp["result"]["content"][0]["text"] == "hello"


def test_standalone_internal_routes_follow_custom_base_path() -> "None":
    mcp = MCP(name="test-mcp", config=MCPConfig(base_path="/api/mcp"))

    @mcp.tool(name="echo", sync_to_thread=False)
    def echo(message: "str") -> "str":
        return message

    @mcp.resource(uri="file://foo/bar", name="foo", sync_to_thread=False)
    def read_foo() -> "str":
        return "foo-content"

    @mcp.prompt(name="greet", sync_to_thread=False)
    def greet(name: "str") -> "str":
        return f"Hello {name}!"

    with TestClient(app=mcp.app) as client:
        tool_direct = client.post("/api/mcp/internal/tools/echo", json={"message": "hello"})
        resource_direct = client.get("/api/mcp/internal/resources/foo/bar")
        prompt_direct = client.get("/api/mcp/internal/prompts/greet")

        old_tool_direct = client.post("/mcp/internal/tools/echo", json={"message": "hello"})
        old_resource_direct = client.get("/mcp/internal/resources/foo/bar")
        old_prompt_direct = client.get("/mcp/internal/prompts/greet")

        tool_call = _rpc(
            client,
            "tools/call",
            {"name": "echo", "arguments": {"message": "hello"}},
            base_path="/api/mcp",
        )
        resource_read = _rpc(client, "resources/read", {"uri": "file://foo/bar"}, base_path="/api/mcp")
        prompt_get = _rpc(
            client,
            "prompts/get",
            {"name": "greet", "arguments": {"name": "Alice"}},
            base_path="/api/mcp",
        )

    assert tool_direct.status_code == 403
    assert resource_direct.status_code == 403
    assert prompt_direct.status_code == 403
    assert old_tool_direct.status_code in (404, 405)
    assert old_resource_direct.status_code in (404, 405)
    assert old_prompt_direct.status_code in (404, 405)
    assert tool_call["result"]["content"][0]["text"] == "hello"
    assert json.loads(resource_read["result"]["contents"][0]["text"]) == "foo-content"
    assert prompt_get["result"]["messages"][0]["content"]["text"] == "Hello Alice!"


def test_standalone_decorator_signatures_mirror_litestar_route_kwargs() -> "None":
    litestar_route_kwargs = set(inspect.signature(get).parameters) - {"path", "kwargs", "name"}
    expected_kwargs = litestar_route_kwargs | {"route_name"}

    for decorator in (MCP.tool, MCP.resource, MCP.prompt):
        params = inspect.signature(decorator).parameters
        missing = sorted(expected_kwargs - set(params))
        assert not missing
        assert params["kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_standalone_tool_decorator_accepts_litestar_route_dependencies() -> "None":
    def provide_suffix() -> "str":
        return "!"

    mcp = MCP(name="test-mcp")

    @mcp.tool(
        name="greet",
        dependencies={"suffix": Provide(provide_suffix, sync_to_thread=False)},
        sync_to_thread=False,
    )
    def greet(name: "str", suffix: "str") -> "dict[str, str]":
        return {"message": f"Hello {name}{suffix}"}

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "tools/call", {"name": "greet", "arguments": {"name": "World"}})

    assert response["result"]["isError"] is False
    assert json.loads(response["result"]["content"][0]["text"]) == {"message": "Hello World!"}


def test_standalone_resource_decorator_accepts_litestar_route_dependencies() -> "None":
    def provide_suffix() -> "str":
        return "!"

    mcp = MCP(name="test-mcp")

    @mcp.resource(
        uri="app://settings",
        name="settings",
        dependencies={"suffix": Provide(provide_suffix, sync_to_thread=False)},
        sync_to_thread=False,
    )
    def settings(suffix: "str") -> "dict[str, str]":
        return {"value": f"enabled{suffix}"}

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "resources/read", {"uri": "app://settings"})

    assert json.loads(response["result"]["contents"][0]["text"]) == {"value": "enabled!"}


def test_standalone_prompt_decorator_accepts_litestar_route_dependencies() -> "None":
    def provide_suffix() -> "str":
        return "!"

    mcp = MCP(name="test-mcp")

    @mcp.prompt(
        name="explain",
        dependencies={"suffix": Provide(provide_suffix, sync_to_thread=False)},
        sync_to_thread=False,
    )
    def explain(topic: "str", suffix: "str") -> "str":
        return f"Explain {topic}{suffix}"

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "prompts/get", {"name": "explain", "arguments": {"topic": "MCP"}})

    content = response["result"]["messages"][0]["content"]
    assert content["text"] == "Explain MCP!"


def test_standalone_tool_decorator_preserves_litestar_route_opt_kwargs() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.tool(
        name="optioned",
        route_name="optioned_route",
        description="Has route opts",
        opt={"exclude_from_auth": True},
        custom_flag="custom",
        response_headers=[ResponseHeader(name="X-Trace", description="Trace id", documentation_only=True)],
        responses={202: ResponseSpec(None, description="Accepted")},
        summary="Optioned tool",
        tags=["Utility"],
        sync_to_thread=False,
    )
    def optioned() -> "str":
        return "ok"

    _ = mcp.app
    handler = cast("Any", mcp.plugin.registry.tools["optioned"])

    assert handler.opt["exclude_from_auth"] is True
    assert handler.opt["custom_flag"] == "custom"
    assert handler.opt["mcp_tool"] == "optioned"
    assert handler.opt["mcp_description"] == "Has route opts"
    assert handler.name == "optioned_route"
    assert handler.description == "Has route opts"
    assert handler.response_headers[0].description == "Trace id"
    assert handler.responses[202].description == "Accepted"
    assert handler.summary == "Optioned tool"
    assert set(handler.tags or []) == {"Utility"}


def test_standalone_tool_decorator_merges_user_guards_with_internal_guard() -> "None":
    guard_calls: list[str] = []

    def user_guard(connection: "Any", _route_handler: "Any") -> "None":
        guard_calls.append(str(connection.url.path))

    mcp = MCP(name="test-mcp")

    @mcp.tool(name="guarded", guards=[user_guard], sync_to_thread=False)
    def guarded() -> "str":
        return "ok"

    with TestClient(app=mcp.app) as client:
        direct = client.post("/mcp/internal/tools/guarded", json={})
        via_mcp = _rpc(client, "tools/call", {"name": "guarded", "arguments": {}})

    assert direct.status_code == 403
    assert via_mcp["result"]["isError"] is False
    assert via_mcp["result"]["content"][0]["text"] == "ok"
    assert guard_calls == ["/mcp/internal/tools/guarded"]


def test_standalone_plugin_coexistence() -> "None":
    class DummyPlugin(InitPluginProtocol):
        def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
            app_config.tags.append("dummy-tag")
            return app_config

    dummy_plugin = DummyPlugin()
    mcp = MCP("coexist-test", plugins=[dummy_plugin])

    app = mcp.app

    assert "dummy-tag" in app.tags
    assert any(isinstance(p, LitestarMCP) for p in app.plugins)
