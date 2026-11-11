import inspect
import json
from typing import TYPE_CHECKING, Annotated, Any, cast

import pytest
from litestar import Litestar, Response, get
from litestar.datastructures import ResponseHeader
from litestar.di import Provide
from litestar.openapi.datastructures import ResponseSpec
from litestar.plugins import InitPluginProtocol
from litestar.testing import TestClient

from litestar_mcp import MCP, MCPStdioContext
from litestar_mcp.mcp.config import UI_EXTENSION, UI_MIME_TYPE, MCPConfig
from litestar_mcp.mcp.content import MCPBlobResource, MCPInputRequiredResult, MCPResourceLink, MCPToolResult
from litestar_mcp.mcp.plugin import LitestarMCP
from tests.unit.conftest import mcp_post

if TYPE_CHECKING:
    from litestar.config.app import AppConfig


def _rpc(
    client: "TestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
    *,
    base_path: "str" = "/mcp",
) -> "dict[str, Any]":
    data: dict[str, Any] = mcp_post(client, method, params, base=base_path).json()
    return data


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
        response = _rpc(client, "server/discover")

    assert response["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "existing-plugin"
    assert response["result"]["instructions"] == "Follow these instructions"


def test_mcp_lazy_app() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="lazy_ping")
    def lazy_ping() -> "str":
        return "pong"

    app = mcp.app
    assert isinstance(app, Litestar)
    assert mcp.app is app
    with TestClient(app=app) as client:
        response = _rpc(client, "tools/call", {"name": "lazy_ping", "arguments": {}})
    assert response["result"]["content"][0]["text"] == "pong"


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

    _ = mcp.app

    registry = mcp.plugin._registry
    assert "my_tool" in registry.tools
    assert "res_name" in registry.resources
    assert "res_name" not in registry.templates
    assert "my_prompt" in registry.prompts

    tool_handler = registry.tools["my_tool"]
    assert tool_handler.opt["mcp_description"] == "tool desc"

    res_handler = registry.resources["res_name"]
    assert res_handler.opt["mcp_resource_description"] == "res desc"
    assert res_handler.opt["mcp_resource_uri"] == "app://my_resource"
    assert "mcp_resource_template" not in res_handler.opt

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
        resource_direct = client.get("/api/mcp/internal/resources/file/foo/bar")
        prompt_direct = client.get("/api/mcp/internal/prompts/greet")

        old_tool_direct = client.post("/mcp/internal/tools/echo", json={"message": "hello"})
        old_resource_direct = client.get("/mcp/internal/resources/file/foo/bar")
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
    assert resource_read["result"]["contents"][0]["text"] == "foo-content"
    assert resource_read["result"]["contents"][0]["mimeType"] == "text/plain"
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


def test_standalone_resource_decorator_sets_mime_type_metadata() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.resource(uri="app://report", name="report", mime_type="application/pdf", sync_to_thread=False)
    def report() -> "dict[str, str]":
        return {"value": "ok"}

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "resources/list")
        read_response = _rpc(client, "resources/read", {"uri": "app://report"})

    resources = response["result"]["resources"]
    report_entry = next(item for item in resources if item["name"] == "report")
    assert report_entry["mimeType"] == "application/pdf"
    assert read_response["result"]["contents"][0]["mimeType"] == "application/json"
    assert json.loads(read_response["result"]["contents"][0]["text"]) == {"value": "ok"}


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


class _Money:
    def __init__(self, cents: "int") -> "None":
        self.cents = cents


def test_standalone_tool_honours_type_encoders_on_the_wire() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="price", type_encoders={_Money: lambda money: f"${money.cents / 100:.2f}"})
    def price() -> "dict[str, Any]":
        return {"amount": _Money(500)}

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "tools/call", {"name": "price", "arguments": {}})

    assert response["result"]["isError"] is False
    assert json.loads(response["result"]["content"][0]["text"]) == {"amount": "$5.00"}


def test_standalone_sync_tool_registers_without_sync_to_thread_warning() -> "None":
    import warnings

    mcp = MCP(name="test-mcp")

    with warnings.catch_warnings():
        warnings.simplefilter("error")

        @mcp.tool(name="plain")
        def plain() -> "str":
            return "ok"

        _ = mcp.app

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "tools/call", {"name": "plain", "arguments": {}})

    assert response["result"]["content"][0]["text"] == "ok"


def test_standalone_run_stdio_forwards_options(monkeypatch: "pytest.MonkeyPatch") -> "None":
    import litestar_mcp.mcp.app as app_module

    captured: dict[str, Any] = {}

    def fake_run_stdio(app: "Litestar", **kwargs: "Any") -> "int":
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(app_module, "run_stdio", fake_run_stdio)
    mcp = MCP(name="test-mcp")
    mcp.run(transport="stdio", shutdown_timeout=30.0, max_message_size=1024)

    assert captured["shutdown_timeout"] == 30.0
    assert captured["max_message_size"] == 1024
    assert isinstance(captured["stdio_context"], MCPStdioContext)


def test_standalone_async_tool_registers_without_sync_to_thread_warning() -> "None":
    import warnings

    mcp = MCP(name="test-mcp")

    with warnings.catch_warnings():
        warnings.simplefilter("error")

        @mcp.tool(name="plain_async")
        async def plain_async() -> "str":
            return "ok"

        _ = mcp.app

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "tools/call", {"name": "plain_async", "arguments": {}})

    assert response["result"]["content"][0]["text"] == "ok"


@pytest.mark.parametrize(
    ("uri", "decorator_kwargs", "is_async", "expected_mime", "expected_text"),
    [
        (
            "ui://app/index.html",
            {"mime_type": "text/html;profile=mcp-app"},
            False,
            "text/html;profile=mcp-app",
            "<h1>Hi</h1>",
        ),
        (
            "docs://guide",
            {"media_type": "text/markdown; variant=GFM"},
            True,
            "text/markdown; variant=GFM",
            "# Guide",
        ),
    ],
)
def test_standalone_resource_raw_str_preserves_parameterized_mime_types(
    uri: "str",
    decorator_kwargs: "dict[str, Any]",
    is_async: "bool",
    expected_mime: "str",
    expected_text: "str",
) -> "None":
    mcp = MCP(name="test-mcp")

    if is_async:

        @mcp.resource(uri, **decorator_kwargs)
        async def async_res() -> "str":
            return expected_text

    else:

        @mcp.resource(uri, sync_to_thread=False, **decorator_kwargs)
        def sync_res() -> "str":
            return expected_text

    with TestClient(app=mcp.app) as client:
        listed = _rpc(client, "resources/list")
        templates = _rpc(client, "resources/templates/list")
        read_resp = _rpc(client, "resources/read", {"uri": uri})

    entry = next(item for item in listed["result"]["resources"] if item["uri"] == uri)
    assert entry["mimeType"] == expected_mime
    assert all(item["uriTemplate"] != uri for item in templates["result"]["resourceTemplates"])
    assert read_resp["result"]["contents"] == [
        {
            "uri": uri,
            "mimeType": expected_mime,
            "text": expected_text,
        }
    ]


def test_standalone_resource_explicit_response_passthrough() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.resource("app://custom", sync_to_thread=False)
    def custom() -> "Response[bytes]":
        return Response(content=b"RAW", media_type="text/csv")

    with TestClient(app=mcp.app) as client:
        read_resp = _rpc(client, "resources/read", {"uri": "app://custom"})

    assert read_resp["result"]["contents"] == [
        {
            "uri": "app://custom",
            "mimeType": "text/csv",
            "text": "RAW",
        }
    ]


def test_standalone_triple_slash_binary_resource_path_and_blob() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.resource("file:///a/b", mime_type="application/octet-stream", sync_to_thread=False)
    def bin_data() -> "bytes":
        return b"\x00\x01"

    with TestClient(app=mcp.app) as client:
        direct = client.get("/mcp/internal/resources/file/a/b")
        listed = _rpc(client, "resources/list")
        templates = _rpc(client, "resources/templates/list")
        read_resp = _rpc(client, "resources/read", {"uri": "file:///a/b"})

    assert all("//" not in route.path for route in mcp.app.routes)
    assert direct.status_code == 403
    entry = next(item for item in listed["result"]["resources"] if item["uri"] == "file:///a/b")
    assert entry["mimeType"] == "application/octet-stream"
    assert templates["result"]["resourceTemplates"] == []
    assert read_resp["result"]["contents"] == [
        {
            "uri": "file:///a/b",
            "mimeType": "application/octet-stream",
            "blob": "AAE=",
        }
    ]


@pytest.mark.parametrize(
    ("return_annotation", "payload"),
    [
        (bytes, b"\xff\xfe"),
        (bytearray, bytearray(b"\xff\xfe")),
        ("bytes", b"\xff\xfe"),
        ("bytearray", bytearray(b"\xff\xfe")),
        (Annotated[bytes, "raw"], b"\xff\xfe"),
    ],
    ids=["bytes", "bytearray", "str_bytes", "str_bytearray", "annotated_bytes"],
)
def test_standalone_binary_return_annotation_defaults_to_octet_stream(
    return_annotation: "Any",
    payload: "bytes | bytearray",
) -> "None":
    mcp = MCP(name="test-mcp")

    def raw_bin() -> "Any":
        return payload

    raw_bin.__annotations__ = {"return": return_annotation}
    mcp.resource("file:///raw.bin", sync_to_thread=False)(raw_bin)

    with TestClient(app=mcp.app) as client:
        listed = _rpc(client, "resources/list")
        read_resp = _rpc(client, "resources/read", {"uri": "file:///raw.bin"})

    entry = next(item for item in listed["result"]["resources"] if item["uri"] == "file:///raw.bin")
    assert entry["mimeType"] == "application/octet-stream"
    assert read_resp["result"]["contents"] == [
        {
            "uri": "file:///raw.bin",
            "mimeType": "application/octet-stream",
            "blob": "//4=",
        }
    ]


def test_standalone_cross_scheme_shared_paths_do_not_collide() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.resource("ui://config", sync_to_thread=False)
    def ui_cfg() -> "str":
        return "ui"

    @mcp.resource("app://config", sync_to_thread=False)
    def app_cfg() -> "str":
        return "app"

    with TestClient(app=mcp.app) as client:
        listed = _rpc(client, "resources/list")
        ui_read = _rpc(client, "resources/read", {"uri": "ui://config"})
        app_read = _rpc(client, "resources/read", {"uri": "app://config"})

    listed_uris = {item["uri"] for item in listed["result"]["resources"]}
    assert {"ui://config", "app://config"} <= listed_uris
    assert ui_read["result"]["contents"][0]["text"] == "ui"
    assert app_read["result"]["contents"][0]["text"] == "app"


@pytest.mark.parametrize(
    ("template_uri", "mime_type", "read_uri", "expected_text", "expected_mime"),
    [
        ("app://items/{item_id}", None, "app://items/abc", "id=abc", "text/plain"),
        (
            "ui://demo/{view}.html",
            "text/html;profile=mcp-app",
            "ui://demo/inspector.html",
            "<main>inspector</main>",
            "text/html;profile=mcp-app",
        ),
    ],
    ids=["full_segment_from_path", "partial_segment_from_query"],
)
def test_standalone_templated_resources_dispatch_full_and_partial_segments(
    template_uri: "str",
    mime_type: "str | None",
    read_uri: "str",
    expected_text: "str",
    expected_mime: "str",
) -> "None":
    mcp = MCP(name="test-mcp")

    if "{item_id}" in template_uri:

        @mcp.resource(template_uri, mime_type=mime_type, sync_to_thread=False)
        def read_item(item_id: "str") -> "str":
            return f"id={item_id}"

    else:

        @mcp.resource(template_uri, mime_type=mime_type, sync_to_thread=False)
        def view_ui(view: "str") -> "str":
            return f"<main>{view}</main>"

    with TestClient(app=mcp.app) as client:
        listed = _rpc(client, "resources/list")
        templates = _rpc(client, "resources/templates/list")
        read_resp = _rpc(client, "resources/read", {"uri": read_uri})

    listed_names = {item["name"] for item in listed["result"]["resources"]}
    assert "read_item" not in listed_names
    assert "view_ui" not in listed_names
    template_entries = templates["result"]["resourceTemplates"]
    assert len(template_entries) == 1
    assert template_entries[0]["uriTemplate"] == template_uri
    assert template_entries[0]["mimeType"] == expected_mime
    assert read_resp["result"]["contents"] == [
        {
            "uri": read_uri,
            "mimeType": expected_mime,
            "text": expected_text,
        }
    ]


def test_standalone_partial_segment_templates_do_not_collide_with_siblings() -> "None":
    mcp = MCP(name="test-mcp")

    @mcp.resource("ui://demo/view.html", mime_type="text/html;profile=mcp-app", sync_to_thread=False)
    def static_view() -> "str":
        return "<main>static</main>"

    @mcp.resource("ui://demo/{view}.html", mime_type="text/html;profile=mcp-app", sync_to_thread=False)
    def dynamic_view(view: "str") -> "str":
        return f"<main>{view}</main>"

    @mcp.resource("ui://demo/{mode}.html", mime_type="text/html;profile=mcp-app", sync_to_thread=False)
    def mode_view(mode: "str") -> "str":
        return f"<section>{mode}</section>"

    with TestClient(app=mcp.app) as client:
        static_read = _rpc(client, "resources/read", {"uri": "ui://demo/view.html"})
        dyn_read = _rpc(client, "resources/read", {"uri": "ui://demo/inspector.html"})

    assert static_read["result"]["contents"][0]["text"] == "<main>static</main>"
    assert dyn_read["result"]["contents"][0]["text"] == "<main>inspector</main>"


@pytest.mark.parametrize(
    ("bad_uri", "expected_match"),
    [
        ("", "Invalid resource URI"),
        ("   ", "Invalid resource URI"),
        ("app://w/{0bad}", "Invalid variable in template"),
        ("app://w/{unclosed", "Unbalanced braces in template"),
    ],
)
def test_standalone_resource_rejects_invalid_uris(bad_uri: "str", expected_match: "str") -> "None":
    mcp = MCP(name="test-mcp")
    with pytest.raises(ValueError, match=expected_match):
        mcp.resource(bad_uri)


@pytest.mark.parametrize(
    ("output_schema", "expected_opt"),
    [
        (
            {"type": "object", "properties": {"id": {"type": "string"}}},
            {"type": "object", "properties": {"id": {"type": "string"}}},
        ),
        (
            {
                "$ref": "#/$defs/Row",
                "$defs": {
                    "Row": {
                        "type": "object",
                        "properties": {"child": {"$ref": "#/$defs/Leaf"}},
                    },
                    "Leaf": {"type": "object", "properties": {"n": {"type": "integer"}}},
                },
            },
            {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Leaf"}},
                "$defs": {"Leaf": {"type": "object", "properties": {"n": {"type": "integer"}}}},
            },
        ),
        (True, True),
        (False, False),
        (None, None),
    ],
    ids=["object_dict", "ref_object_unwrapped", "bool_true", "bool_false", "none_omitted"],
)
def test_standalone_tool_stores_output_schema_opt(output_schema: Any, expected_opt: Any) -> None:
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="schema_tool", output_schema=output_schema, sync_to_thread=False)
    def schema_tool() -> dict[str, Any]:
        return {"id": "1"}

    _ = mcp.app
    handler = cast("Any", mcp.plugin.registry.tools["schema_tool"])
    if expected_opt is None:
        assert "mcp_output_schema" not in handler.opt
    elif isinstance(expected_opt, bool):
        assert handler.opt["mcp_output_schema"] is expected_opt
    else:
        assert handler.opt["mcp_output_schema"] == expected_opt


@pytest.mark.parametrize(
    ("bad_schema", "exc_type", "expected_match"),
    [
        ("bad", TypeError, "output_schema must be a mapping, bool, or None"),
        (123, TypeError, "output_schema must be a mapping, bool, or None"),
        (["x"], TypeError, "output_schema must be a mapping, bool, or None"),
        ({"type": "string"}, ValueError, "output_schema mapping must resolve to root type 'object'"),
        ({"type": "array", "items": {"type": "integer"}}, ValueError, "root type 'object'"),
        ({}, ValueError, "root type 'object'"),
        ({"$ref": "#/$defs/Missing"}, ValueError, "root type 'object'"),
    ],
    ids=["str", "int", "list", "string_schema", "array_schema", "empty_dict", "dangling_ref"],
)
def test_standalone_tool_rejects_invalid_output_schema(
    bad_schema: Any,
    exc_type: type[Exception],
    expected_match: str,
) -> None:
    mcp = MCP(name="test-mcp")
    with pytest.raises(exc_type, match=expected_match):
        mcp.tool(name="bad_tool", output_schema=bad_schema)


@pytest.mark.parametrize(
    ("helper_value", "expected_result"),
    [
        (
            MCPToolResult(content="done", structured_content={"count": 3}),
            {
                "content": [{"type": "text", "text": "done"}],
                "structuredContent": {"count": 3},
                "isError": False,
            },
        ),
        (
            MCPBlobResource(uri="app://bin/item", data=b"\x01\x02", mime_type="application/octet-stream"),
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "app://bin/item",
                            "mimeType": "application/octet-stream",
                            "blob": "AQI=",
                        },
                    },
                ],
                "isError": False,
            },
        ),
        (
            MCPResourceLink(name="guide", uri="app://docs/guide", mime_type="text/markdown"),
            {
                "content": [
                    {
                        "type": "resource_link",
                        "name": "guide",
                        "uri": "app://docs/guide",
                        "mimeType": "text/markdown",
                    },
                ],
                "isError": False,
            },
        ),
        (
            MCPInputRequiredResult(
                input_requests={"confirm": {"prompt": "Proceed?"}},
                request_state="sig-state",
            ),
            {
                "resultType": "input_required",
                "inputRequests": {"confirm": {"prompt": "Proceed?"}},
                "requestState": "sig-state",
            },
        ),
    ],
    ids=["mcp_tool_result", "mcp_blob_resource", "mcp_resource_link", "mcp_input_required"],
)
def test_standalone_tool_passes_through_mcp_content_helpers(
    helper_value: Any,
    expected_result: dict[str, Any],
) -> None:
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="emit_helper", sync_to_thread=False)
    def emit_helper() -> Any:
        return helper_value

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "tools/call", {"name": "emit_helper", "arguments": {}})

    actual = {
        key: value
        for key, value in response["result"].items()
        if key != "_meta" and (key != "resultType" or "resultType" in expected_result)
    }
    assert actual == expected_result


@pytest.mark.parametrize(
    ("app_encoders", "route_encoders", "return_value", "expected_decoded"),
    [
        (None, {bytes: lambda b: b.hex()}, b"\xab\xcd", "abcd"),
        ({bytes: lambda b: b.hex()}, None, b"\x01\x02", "0102"),
        ({bytes: lambda b: b.hex()}, {bytes: lambda b: f"0x{b.hex()}"}, b"\x0f", "0x0f"),
        (None, {bytearray: lambda b: bytes(b).hex()}, bytearray(b"\xca\xfe"), "cafe"),
        (None, {str: str.upper}, "hello", "HELLO"),
        (None, {type(None): lambda _: {"empty": True}}, None, {"empty": True}),
        (None, {_Money: lambda m: f"${m.cents / 100:.2f}"}, _Money(500), "$5.00"),
        ({_Money: lambda m: f"${m.cents / 100:.2f}"}, None, {"amount": _Money(500)}, {"amount": "$5.00"}),
    ],
    ids=[
        "route_bytes_hex",
        "app_bytes_hex",
        "route_overrides_app_bytes",
        "route_bytearray_hex",
        "route_str_encoder",
        "route_none_to_dict",
        "route_direct_custom_type",
        "app_nested_custom_type",
    ],
)
def test_standalone_tool_applies_merged_primitive_and_custom_type_encoders(
    app_encoders: dict[Any, Any] | None,
    route_encoders: dict[Any, Any] | None,
    return_value: Any,
    expected_decoded: Any,
) -> None:
    mcp_kwargs: dict[str, Any] = {}
    if app_encoders is not None:
        mcp_kwargs["type_encoders"] = app_encoders
    mcp = MCP(name="test-mcp", **mcp_kwargs)

    @mcp.tool(name="encoded_tool", type_encoders=route_encoders, sync_to_thread=False)
    def encoded_tool() -> Any:
        return return_value

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "tools/call", {"name": "encoded_tool", "arguments": {}})

    assert response["result"]["isError"] is False
    text = response["result"]["content"][0]["text"]
    if isinstance(expected_decoded, str):
        assert text == expected_decoded
    else:
        assert json.loads(text) == expected_decoded


def test_standalone_mcp_app_descriptors_and_empty_arguments_roundtrip() -> None:
    mcp = MCP(name="ui-demo")
    tool_icons = [{"src": "https://example.com/tool.svg"}]
    res_icons = [{"src": "https://example.com/res.svg"}]
    tpl_icons = [{"src": "https://example.com/tpl.svg"}]
    prompt_icons = [{"src": "https://example.com/prompt.svg"}]
    explicit_args = [{"name": "topic", "required": False}]

    @mcp.tool(
        name="inspect_item",
        title="  Inspect Item  ",
        icons=tool_icons,
        meta={"ui": {"resourceUri": "ui://demo/app.html"}},
        output_schema=True,
        sync_to_thread=False,
    )
    def inspect_item() -> dict[str, bool]:
        return {"ok": True}

    @mcp.resource(
        "ui://demo/app.html",
        name="app_ui",
        title="  App UI  ",
        mime_type=UI_MIME_TYPE,
        icons=res_icons,
        meta={"ui": {"prefersBorder": True}},
        sync_to_thread=False,
    )
    def app_ui() -> str:
        return "<main>Static</main>"

    @mcp.resource(
        "ui://demo/{slug}.html",
        name="view_ui",
        title="  View UI  ",
        mime_type=UI_MIME_TYPE,
        icons=tpl_icons,
        meta={"ui": {"prefersBorder": False}},
        sync_to_thread=False,
    )
    def view_ui(slug: str) -> str:
        return f"<main>{slug}</main>"

    @mcp.prompt(
        name="guide",
        title="  Guide  ",
        arguments=explicit_args,
        icons=prompt_icons,
        meta={"tier": "gold"},
        sync_to_thread=False,
    )
    def guide(topic: str = "mcp") -> str:
        return f"Guide: {topic}"

    @mcp.prompt(name="no_args", arguments=[], sync_to_thread=False)
    def no_args(ignored: str = "default") -> str:
        return ignored

    with TestClient(app=mcp.app) as client:
        discover = _rpc(client, "server/discover")["result"]
        tools = {item["name"]: item for item in _rpc(client, "tools/list")["result"]["tools"]}
        resources = {item["name"]: item for item in _rpc(client, "resources/list")["result"]["resources"]}
        templates = {
            item["name"]: item for item in _rpc(client, "resources/templates/list")["result"]["resourceTemplates"]
        }
        prompts = {item["name"]: item for item in _rpc(client, "prompts/list")["result"]["prompts"]}
        static_read = _rpc(client, "resources/read", {"uri": "ui://demo/app.html"})["result"]
        tpl_read = _rpc(client, "resources/read", {"uri": "ui://demo/detail.html"})["result"]

    assert discover["capabilities"]["extensions"][UI_EXTENSION] == {"mimeTypes": [UI_MIME_TYPE]}
    assert tools["inspect_item"]["title"] == "Inspect Item"
    assert tools["inspect_item"]["icons"] == tool_icons
    assert tools["inspect_item"]["_meta"] == {"ui": {"resourceUri": "ui://demo/app.html"}}
    assert tools["inspect_item"]["outputSchema"]["type"] == "object"

    assert resources["app_ui"]["title"] == "App UI"
    assert resources["app_ui"]["icons"] == res_icons
    assert resources["app_ui"]["_meta"] == {"ui": {"prefersBorder": True}}

    assert templates["view_ui"]["title"] == "View UI"
    assert templates["view_ui"]["icons"] == tpl_icons
    assert templates["view_ui"]["_meta"] == {"ui": {"prefersBorder": False}}

    assert prompts["guide"]["title"] == "Guide"
    assert prompts["guide"]["arguments"] == explicit_args
    assert prompts["guide"]["icons"] == prompt_icons
    assert prompts["guide"]["_meta"] == {"tier": "gold"}
    no_args_handler = mcp.plugin.registry.prompts["no_args"].handler
    assert no_args_handler is not None
    assert no_args_handler.opt["mcp_prompt_arguments"] == []
    assert "arguments" not in prompts["no_args"]

    assert static_read["contents"][0]["_meta"] == {"ui": {"prefersBorder": True}}
    assert tpl_read["contents"][0]["_meta"] == {"ui": {"prefersBorder": False}}


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt"])
def test_standalone_decorators_preserve_user_opt_when_descriptor_kwargs_blank(kind: str) -> None:
    mcp = MCP(name="opt-preserve")
    user_icons = [{"src": "https://example.com/user.svg"}]

    if kind == "tool":
        user_opt: dict[str, Any] = {
            "mcp_tool_title": "Keep Tool",
            "mcp_tool_icons": user_icons,
            "mcp_tool_meta": {"a": 1},
        }

        @mcp.tool(name="u_tool", title="   ", icons=[], meta={}, opt=user_opt, sync_to_thread=False)
        def u_tool() -> str:
            return "ok"

        with TestClient(app=mcp.app) as client:
            entry = next(item for item in _rpc(client, "tools/list")["result"]["tools"] if item["name"] == "u_tool")
        assert entry["title"] == "Keep Tool"
        assert entry["icons"] == user_icons
        assert entry["_meta"] == {"a": 1}
    elif kind == "resource":
        user_opt = {
            "mcp_resource_title": "Keep Res",
            "mcp_resource_icons": user_icons,
            "mcp_resource_meta": {"b": 2},
        }

        @mcp.resource(
            "ui://u/view.html",
            name="u_res",
            title="   ",
            icons=[],
            meta={},
            opt=user_opt,
            sync_to_thread=False,
        )
        def u_res() -> str:
            return "ok"

        with TestClient(app=mcp.app) as client:
            entry = next(
                item for item in _rpc(client, "resources/list")["result"]["resources"] if item["name"] == "u_res"
            )
        assert entry["title"] == "Keep Res"
        assert entry["icons"] == user_icons
        assert entry["_meta"] == {"b": 2}
    else:
        user_opt = {
            "mcp_prompt_title": "Keep Prompt",
            "mcp_prompt_icons": user_icons,
            "mcp_prompt_meta": {"c": 3},
        }

        @mcp.prompt(
            name="u_prompt",
            title="   ",
            icons=[],
            meta={},
            opt=user_opt,
            sync_to_thread=False,
        )
        def u_prompt() -> str:
            return "ok"

        with TestClient(app=mcp.app) as client:
            prompts = _rpc(client, "prompts/list")["result"]["prompts"]
            entry = next(item for item in prompts if item["name"] == "u_prompt")
        assert entry["title"] == "Keep Prompt"
        assert entry["icons"] == user_icons
        assert entry["_meta"] == {"c": 3}
