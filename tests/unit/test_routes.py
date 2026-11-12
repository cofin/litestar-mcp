"""Unit tests for MCP route resource MIME handling, outputSchema discovery, and structuredContent."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import msgspec
import pytest
from litestar import Controller, Litestar, Response, Router, get, post
from litestar.params import Parameter
from litestar.testing import AsyncTestClient, TestClient
from pydantic import BaseModel, ConfigDict
from typing_extensions import NotRequired, TypedDict

from litestar_mcp import (
    MCP,
    LitestarMCP,
    MCPBlobResource,
    MCPConfig,
    MCPOptKeys,
    MCPSkillsConfig,
    MCPToolResult,
    mcp_resource,
    mcp_tool,
    tool,
)
from litestar_mcp.core.serialization import from_json
from litestar_mcp.mcp.config import UI_EXTENSION, UI_MIME_TYPE
from litestar_mcp.mcp.service import MCPRequestContext, get_mcp_request_context
from tests.unit.conftest import mcp_envelope, mcp_post, mcp_post_async

APP_HTML = "<!doctype html><html><body><h1>Explorer</h1></body></html>"
TASKS_CAPABILITIES: dict[str, Any] = {"extensions": {"io.modelcontextprotocol/tasks": {}}}


class _RouteLeaf(msgspec.Struct, rename="camel"):
    leaf_code: str


class _RouteCamelOut(msgspec.Struct, rename="camel"):
    item_name: str
    leaf: _RouteLeaf
    total_count: int = 1


@dataclass
class _RouteDataclassOut:
    label: str
    active: bool = True


class _RoutePydanticOut(BaseModel):
    slug: str
    score: int


class _RouteTypedDictOut(TypedDict):
    order_id: str
    notes: NotRequired[str]


def _make_auto_output_schema_app() -> Litestar:
    @get("/camel", mcp_tool="camel_struct", sync_to_thread=False)
    def camel_struct() -> _RouteCamelOut:
        return _RouteCamelOut(item_name="alpha", leaf=_RouteLeaf(leaf_code="L1"), total_count=4)

    @post("/annotated", mcp_tool="annotated_struct", sync_to_thread=False)
    def annotated_struct() -> Annotated[_RouteCamelOut, Parameter(description="d"), msgspec.Meta(title="T")]:
        return _RouteCamelOut(item_name="beta", leaf=_RouteLeaf(leaf_code="L2"), total_count=2)

    @get("/dc", mcp_tool="dc_out", sync_to_thread=False)
    def dc_out() -> _RouteDataclassOut:
        return _RouteDataclassOut(label="ready", active=True)

    @get("/pyd", mcp_tool="pyd_out", sync_to_thread=False)
    def pyd_out() -> _RoutePydanticOut:
        return _RoutePydanticOut(slug="fast", score=9)

    @get("/td", mcp_tool="td_out", sync_to_thread=False)
    def td_out() -> _RouteTypedDictOut:
        return {"order_id": "ord-1"}

    @get("/dict-block", mcp_tool="dict_block", sync_to_thread=False)
    def dict_block() -> dict[str, Any]:
        return {"type": "text", "text": "domain text", "extra": 7}

    @get("/opt-out", mcp_tool="opted_out", mcp_output_schema=False, sync_to_thread=False)
    def opted_out() -> _RouteCamelOut:
        return _RouteCamelOut(item_name="skip", leaf=_RouteLeaf(leaf_code="L0"))

    @get("/str-ret", mcp_tool="scalar_str", sync_to_thread=False)
    def scalar_str() -> str:
        return "plain"

    @post("/none-ret", mcp_tool="none_ret", status_code=204, sync_to_thread=False)
    def none_ret() -> None:
        pass

    return Litestar(
        route_handlers=[
            camel_struct,
            annotated_struct,
            dc_out,
            pyd_out,
            td_out,
            dict_block,
            opted_out,
            scalar_str,
            none_ret,
        ],
        plugins=[LitestarMCP(MCPConfig(auto_output_schema=True))],
    )


def test_resource_read_preserves_mcp_app_mime_parameter() -> None:
    """resources/list and resources/read preserve text/html;profile=mcp-app (issue #106)."""

    @get(
        "/ui/explorer",
        mcp_resource="explorer_ui",
        mcp_resource_uri="ui://demo/explorer.html",
        mcp_resource_mime_type="text/html;profile=mcp-app",
        sync_to_thread=False,
    )
    def explorer_ui() -> Response[str]:
        return Response(content=APP_HTML, media_type="text/html;profile=mcp-app")

    app = Litestar(route_handlers=[explorer_ui], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        list_resp = mcp_post(client, "resources/list").json()
        read_resp = mcp_post(client, "resources/read", {"uri": "ui://demo/explorer.html"}).json()

    resources = list_resp["result"]["resources"]
    explorer_entry = next(item for item in resources if item["uri"] == "ui://demo/explorer.html")
    assert explorer_entry["mimeType"] == "text/html;profile=mcp-app"
    assert read_resp["result"]["contents"] == [
        {
            "uri": "ui://demo/explorer.html",
            "mimeType": "text/html;profile=mcp-app",
            "text": APP_HTML,
        }
    ]


@pytest.mark.parametrize(
    ("uri", "declared_mime", "response_media_type", "payload", "expected_mime", "expected_text"),
    [
        (
            "ui://demo/explorer.html",
            "text/html;profile=mcp-app",
            "text/html;profile=mcp-app",
            APP_HTML,
            "text/html;profile=mcp-app",
            APP_HTML,
        ),
        (
            "ui://demo/fallback.html",
            "text/html;profile=mcp-app",
            "text/html",
            APP_HTML,
            "text/html;profile=mcp-app",
            APP_HTML,
        ),
        (
            "litestar://guide_md",
            "text/markdown; variant=GFM",
            "text/markdown; variant=GFM",
            "# Guide\n\n- item\n",
            "text/markdown; variant=GFM",
            "# Guide\n\n- item\n",
        ),
        (
            "litestar://schema_doc",
            "application/json;profile=mcp-schema",
            "application/json;profile=mcp-schema",
            b'{"type":"object"}',
            "application/json;profile=mcp-schema",
            '{"type":"object"}',
        ),
    ],
    ids=[
        "mcp_app_response_and_declared",
        "mcp_app_declared_with_bare_response_media_type",
        "parameterized_text_markdown",
        "parameterized_application_json",
    ],
)
def test_resources_read_preserves_mime_parameters_and_decodes_utf8_text(
    uri: str,
    declared_mime: str,
    response_media_type: str,
    payload: Any,
    expected_mime: str,
    expected_text: str,
) -> None:
    """Parameterized MIME types preserve non-charset parameters and decode UTF-8 bodies as text."""

    @get(
        "/resource",
        mcp_resource="test_resource",
        mcp_resource_uri=uri,
        mcp_resource_mime_type=declared_mime,
        sync_to_thread=False,
    )
    def resource_handler() -> Response[Any]:
        return Response(content=payload, media_type=response_media_type)

    app = Litestar(route_handlers=[resource_handler], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        read_resp = mcp_post(client, "resources/read", {"uri": uri}).json()

    contents = read_resp["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["uri"] == uri
    assert contents[0]["mimeType"] == expected_mime
    assert contents[0]["text"] == expected_text
    assert "blob" not in contents[0]


def test_resource_template_read_preserves_declared_mime_parameters_on_bare_response() -> None:
    """URI-template resources also preserve declared MIME parameters when response media_type is bare."""

    @get(
        "/ui/{view:str}",
        mcp_resource="view_ui",
        mcp_resource_template="ui://demo/{view}.html",
        mcp_resource_mime_type="text/html;profile=mcp-app",
        sync_to_thread=False,
    )
    def view_ui(view: str) -> Response[str]:
        return Response(content=f"<main>{view}</main>", media_type="text/html")

    app = Litestar(route_handlers=[view_ui], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        templates_resp = mcp_post(client, "resources/templates/list").json()
        read_resp = mcp_post(client, "resources/read", {"uri": "ui://demo/inspector.html"}).json()

    templates = templates_resp["result"]["resourceTemplates"]
    template_entry = next(item for item in templates if item["name"] == "view_ui")
    assert template_entry["mimeType"] == "text/html;profile=mcp-app"
    assert read_resp["result"]["contents"] == [
        {
            "uri": "ui://demo/inspector.html",
            "mimeType": "text/html;profile=mcp-app",
            "text": "<main>inspector</main>",
        }
    ]


@pytest.mark.parametrize(
    ("mode", "read_uri", "expected_text"),
    [
        ("decorator_static", "ui://app/dashboard.html", "<h1>App</h1>"),
        ("opt_static", "ui://app/opt.html", "<h1>Opt</h1>"),
        ("opt_template", "ui://tpl/42", "<p>42</p>"),
        ("decorator_template", "ui://dectpl/99", "<div>99</div>"),
    ],
)
def test_raw_str_handlers_preserve_explicit_mcp_mime_over_litestar_text_plain_default(
    mode: str,
    read_uri: str,
    expected_text: str,
) -> None:
    """Raw str static and templated handlers preserve explicit parameterized MIME over implicit text/plain."""
    mime = "text/html;profile=mcp-app"
    if mode == "decorator_static":

        @get("/dash", sync_to_thread=False)
        @mcp_resource("dash", uri=read_uri, mime_type=mime)
        def dash() -> str:
            return "<h1>App</h1>"

        target: Any = dash
        is_template = False
    elif mode == "opt_static":

        @get(
            "/opt-dash",
            mcp_resource="opt_dash",
            mcp_resource_uri=read_uri,
            mcp_resource_mime_type=mime,
            sync_to_thread=False,
        )
        def opt_dash() -> str:
            return "<h1>Opt</h1>"

        target = opt_dash
        is_template = False
    elif mode == "opt_template":

        @get(
            "/t/{item_id:str}",
            mcp_resource="tpl",
            mcp_resource_template="ui://tpl/{item_id}",
            mcp_resource_mime_type=mime,
            sync_to_thread=False,
        )
        def tpl(item_id: str) -> str:
            return f"<p>{item_id}</p>"

        target = tpl
        is_template = True
    else:

        @mcp_resource("dec_tpl", uri="ui://dectpl/{item_id}", mime_type=mime)
        @get("/dt/{item_id:str}", sync_to_thread=False)
        def dec_tpl(item_id: str) -> str:
            return f"<div>{item_id}</div>"

        target = dec_tpl
        is_template = True

    app = Litestar(route_handlers=[target], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        list_resp = mcp_post(client, "resources/list").json()
        templates_resp = mcp_post(client, "resources/templates/list").json()
        read_resp = mcp_post(client, "resources/read", {"uri": read_uri}).json()

    if is_template:
        assert len(templates_resp["result"]["resourceTemplates"]) == 1
        assert templates_resp["result"]["resourceTemplates"][0]["mimeType"] == mime
    else:
        entry = next(item for item in list_resp["result"]["resources"] if item["uri"] == read_uri)
        assert entry["mimeType"] == mime
        assert templates_resp["result"]["resourceTemplates"] == []

    assert read_resp["result"]["contents"] == [
        {
            "uri": read_uri,
            "mimeType": mime,
            "text": expected_text,
        }
    ]


@pytest.mark.parametrize(
    ("res_name", "declared_mime", "payload", "expected_list_mime", "expected_read_mime"),
    [
        ("geo", "application/vnd.geo+json", {"type": "Point"}, "application/vnd.geo+json", "application/vnd.geo+json"),
        ("report", "application/pdf", {"value": "ok"}, "application/pdf", "application/json"),
    ],
)
def test_dict_resource_explicit_mime_preserves_json_text_readability(
    res_name: str,
    declared_mime: str,
    payload: dict[str, str],
    expected_list_mime: str,
    expected_read_mime: str,
) -> None:
    """Dict resources honor text-compatible +json MIME overrides while keeping binary MIME dicts readable as JSON."""

    @get(
        "/dict-res",
        mcp_resource=res_name,
        mcp_resource_mime_type=declared_mime,
        sync_to_thread=False,
    )
    def dict_handler() -> dict[str, str]:
        return payload

    uri = f"litestar://{res_name}"
    app = Litestar(route_handlers=[dict_handler], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        list_resp = mcp_post(client, "resources/list").json()
        read_resp = mcp_post(client, "resources/read", {"uri": uri}).json()

    entry = next(item for item in list_resp["result"]["resources"] if item["name"] == res_name)
    assert entry["mimeType"] == expected_list_mime
    content = read_resp["result"]["contents"][0]
    assert content["uri"] == uri
    assert content["mimeType"] == expected_read_mime
    assert json.loads(content["text"]) == payload
    assert "blob" not in content


@pytest.mark.parametrize(
    ("tool_name", "expected_props", "expected_defs", "expected_title_desc", "expected_structured"),
    [
        (
            "camel_struct",
            {"itemName", "leaf", "totalCount"},
            {"_RouteLeaf"},
            None,
            {"itemName": "alpha", "leaf": {"leafCode": "L1"}, "totalCount": 4},
        ),
        (
            "annotated_struct",
            {"itemName", "leaf", "totalCount"},
            {"_RouteLeaf"},
            ("T", "d"),
            {"itemName": "beta", "leaf": {"leafCode": "L2"}, "totalCount": 2},
        ),
        (
            "dc_out",
            {"label", "active"},
            set(),
            None,
            {"label": "ready", "active": True},
        ),
        (
            "pyd_out",
            {"slug", "score"},
            set(),
            None,
            {"slug": "fast", "score": 9},
        ),
        (
            "td_out",
            {"order_id", "notes"},
            set(),
            None,
            {"order_id": "ord-1"},
        ),
        (
            "dict_block",
            set(),
            set(),
            None,
            {"type": "text", "text": "domain text", "extra": 7},
        ),
    ],
    ids=["camel_struct", "annotated_struct", "dataclass", "pydantic", "typed_dict", "dict_with_text_keys"],
)
def test_routes_auto_output_schema_models_populate_structured_content(
    tool_name: str,
    expected_props: set[str],
    expected_defs: set[str],
    expected_title_desc: tuple[str, str] | None,
    expected_structured: dict[str, Any],
) -> None:
    app = _make_auto_output_schema_app()
    with TestClient(app=app) as client:
        listed = mcp_post(client, "tools/list").json()["result"]["tools"]
        called = mcp_post(client, "tools/call", {"name": tool_name, "arguments": {}}).json()["result"]

    entry = next(item for item in listed if item["name"] == tool_name)
    output_schema = entry["outputSchema"]
    assert output_schema["type"] == "object"
    assert "$ref" not in output_schema
    if expected_props:
        assert set(output_schema["properties"]) == expected_props
    if expected_defs:
        assert set(output_schema["$defs"]) == expected_defs
    else:
        assert "$defs" not in output_schema
    if expected_title_desc is not None:
        assert output_schema["title"] == expected_title_desc[0]
        assert output_schema["description"] == expected_title_desc[1]

    assert called["isError"] is False
    assert called["structuredContent"] == expected_structured
    assert json.loads(called["content"][0]["text"]) == expected_structured


@pytest.mark.parametrize("tool_name", ["opted_out", "scalar_str", "none_ret"])
def test_routes_auto_output_schema_omits_opted_out_scalar_and_none_returns(tool_name: str) -> None:
    app = _make_auto_output_schema_app()
    with TestClient(app=app) as client:
        listed = mcp_post(client, "tools/list").json()["result"]["tools"]
        called = mcp_post(client, "tools/call", {"name": tool_name, "arguments": {}}).json()["result"]

    entry = next(item for item in listed if item["name"] == tool_name)
    assert "outputSchema" not in entry
    assert called["isError"] is False
    assert "structuredContent" not in called


@pytest.mark.parametrize(
    "surface",
    ["mcp_tool_decorator", "standalone_mcp", "registered_tool", "controller_tool"],
)
def test_routes_explicit_output_schema_across_registration_surfaces(surface: str) -> None:
    expected_payload = {"itemName": "gamma", "leaf": {"leafCode": "L3"}, "totalCount": 5}

    if surface == "mcp_tool_decorator":

        @mcp_tool("surface_tool", output_schema=True)
        @get("/surface", sync_to_thread=False)
        def route_fn() -> _RouteCamelOut:
            return _RouteCamelOut(item_name="gamma", leaf=_RouteLeaf(leaf_code="L3"), total_count=5)

        app = Litestar(route_handlers=[route_fn], plugins=[LitestarMCP()])
    elif surface == "standalone_mcp":
        mcp = MCP(name="surface-mcp")

        @mcp.tool(name="surface_tool", output_schema=True, sync_to_thread=False)
        def standalone_fn() -> _RouteCamelOut:
            return _RouteCamelOut(item_name="gamma", leaf=_RouteLeaf(leaf_code="L3"), total_count=5)

        app = mcp.app
    elif surface == "registered_tool":

        @tool(name="surface_tool", output_schema=True)
        def registered_fn() -> _RouteCamelOut:
            return _RouteCamelOut(item_name="gamma", leaf=_RouteLeaf(leaf_code="L3"), total_count=5)

        plugin = LitestarMCP()
        plugin.register_tool(registered_fn)
        app = Litestar(route_handlers=[], plugins=[plugin])
    else:

        class SurfaceController(Controller):
            path = "/ctrl"

            @mcp_tool("surface_tool", output_schema=True)
            @get("/", sync_to_thread=False)
            def ctrl_fn(self) -> _RouteCamelOut:
                return _RouteCamelOut(item_name="gamma", leaf=_RouteLeaf(leaf_code="L3"), total_count=5)

        app = Litestar(route_handlers=[SurfaceController], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        listed = mcp_post(client, "tools/list").json()["result"]["tools"]
        called = mcp_post(client, "tools/call", {"name": "surface_tool", "arguments": {}}).json()["result"]

    entry = next(item for item in listed if item["name"] == "surface_tool")
    assert entry["outputSchema"]["type"] == "object"
    assert set(entry["outputSchema"]["properties"]) == {"itemName", "leaf", "totalCount"}
    assert set(entry["outputSchema"]["$defs"]) == {"_RouteLeaf"}
    assert called["isError"] is False
    assert called["structuredContent"] == expected_payload
    assert json.loads(called["content"][0]["text"]) == expected_payload


@pytest.mark.asyncio
async def test_routes_background_task_populates_structured_content() -> None:
    @post("/bg-item", mcp_tool="bg_item", mcp_task=True)
    async def bg_item() -> _RouteCamelOut:
        return _RouteCamelOut(item_name="bg", leaf=_RouteLeaf(leaf_code="LT"), total_count=3)

    app = Litestar(
        route_handlers=[bg_item],
        plugins=[LitestarMCP(MCPConfig(tasks=True, auto_output_schema=True))],
    )
    task_meta = {"io.modelcontextprotocol/clientCapabilities": TASKS_CAPABILITIES}

    async with AsyncTestClient(app=app) as client:
        start = (
            await mcp_post_async(
                client,
                "tools/call",
                {"name": "bg_item", "arguments": {}, "_meta": task_meta},
            )
        ).json()["result"]
        assert start["resultType"] == "task"
        task_id = start["taskId"]

        record: dict[str, Any] = {}
        while record.get("status") not in {"completed", "failed", "cancelled"}:
            await asyncio.sleep(0.01)
            record = (
                await mcp_post_async(
                    client,
                    "tasks/get",
                    {"taskId": task_id, "_meta": task_meta},
                )
            ).json()["result"]

    assert record["status"] == "completed"
    task_result = record["result"]
    expected = {"itemName": "bg", "leaf": {"leafCode": "LT"}, "totalCount": 3}
    assert task_result["isError"] is False
    assert task_result["structuredContent"] == expected
    assert json.loads(task_result["content"][0]["text"]) == expected


@pytest.mark.parametrize(
    ("non_object_payload", "expected_type_name"),
    [
        (Response(content="text", media_type="text/plain"), "str"),
        ([1, 2], "list"),
        (42, "int"),
        (Response(content=b"null", media_type="application/json"), "NoneType"),
        (MCPBlobResource(uri="app://blob", data=b"\x00\x01"), "MCPBlobResource"),
        (MCPToolResult(content="x", structured_content="bad"), "str"),
        (MCPToolResult(content="x", structured_content=None), "NoneType"),
    ],
    ids=["str", "list", "int", "null_json", "blob_resource", "tool_result_str", "tool_result_none"],
)
def test_routes_object_output_schema_rejects_non_object_returns(
    non_object_payload: Any,
    expected_type_name: str,
) -> None:
    @mcp_tool(
        "strict_obj",
        output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
    )
    @get("/strict-obj", sync_to_thread=False)
    def strict_obj() -> Any:
        return non_object_payload

    app = Litestar(route_handlers=[strict_obj], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        called = mcp_post(client, "tools/call", {"name": "strict_obj", "arguments": {}}).json()["result"]

    assert called["isError"] is True
    assert "structuredContent" not in called
    error_body = json.loads(called["content"][0]["text"])
    assert expected_type_name in error_body["error"]


@pytest.mark.parametrize("bad_opt", ["invalid", {"type": "string"}])
def test_routes_invalid_opt_output_schema_returns_jsonrpc_internal_error(bad_opt: Any) -> None:
    @get("/bad-schema", mcp_tool="bad_schema", mcp_output_schema=bad_opt, sync_to_thread=False)
    def bad_schema() -> dict[str, str]:
        return {"id": "1"}

    app = Litestar(route_handlers=[bad_schema], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        list_body = mcp_post(client, "tools/list").json()
        call_body = mcp_post(client, "tools/call", {"name": "bad_schema", "arguments": {}}).json()

    assert list_body["error"]["code"] == -32603
    assert call_body["error"]["code"] == -32603


def test_routes_errors_and_renamed_output_schema_opt_key() -> None:
    @get(
        "/err-tool",
        mcp_tool="err_tool",
        mcp_output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        sync_to_thread=False,
    )
    def err_tool(count: int) -> dict[str, str]:
        if count < 0:
            msg = "boom"
            raise ValueError(msg)
        return {"id": str(count)}

    error_app = Litestar(route_handlers=[err_tool], plugins=[LitestarMCP()])
    with TestClient(app=error_app) as client:
        raised = mcp_post(client, "tools/call", {"name": "err_tool", "arguments": {"count": -1}}).json()["result"]
        bad_arg = mcp_post(client, "tools/call", {"name": "err_tool", "arguments": {"count": "nope"}}).json()["result"]

    assert raised["isError"] is True
    assert "structuredContent" not in raised
    assert "does not match declared object outputSchema" not in raised["content"][0]["text"]
    assert bad_arg["isError"] is True
    assert "structuredContent" not in bad_arg
    assert "Invalid tool arguments" in bad_arg["content"][0]["text"]

    @get(
        "/renamed-active",
        mcp_tool="renamed_active",
        x_output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        sync_to_thread=False,
    )
    def renamed_active() -> dict[str, str]:
        return {"id": "ok"}

    @get(
        "/renamed-ignored",
        mcp_tool="renamed_ignored",
        mcp_output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        sync_to_thread=False,
    )
    def renamed_ignored() -> dict[str, str]:
        return {"id": "plain"}

    renamed_app = Litestar(
        route_handlers=[renamed_active, renamed_ignored],
        plugins=[LitestarMCP(MCPConfig(opt_keys=MCPOptKeys(output_schema="x_output_schema")))],
    )
    with TestClient(app=renamed_app) as client:
        tools = {t["name"]: t for t in mcp_post(client, "tools/list").json()["result"]["tools"]}
        active_call = mcp_post(client, "tools/call", {"name": "renamed_active", "arguments": {}}).json()["result"]
        ignored_call = mcp_post(client, "tools/call", {"name": "renamed_ignored", "arguments": {}}).json()["result"]

    assert tools["renamed_active"]["outputSchema"] == {"type": "object", "properties": {"id": {"type": "string"}}}
    assert "outputSchema" not in tools["renamed_ignored"]
    assert active_call["structuredContent"] == {"id": "ok"}
    assert "structuredContent" not in ignored_call


def test_routes_plain_tool_without_output_schema_omits_structured_content() -> None:
    @get("/plain-dict", mcp_tool="plain_dict", sync_to_thread=False)
    def plain_dict() -> dict[str, bool]:
        return {"ok": True}

    @get("/null-sc", mcp_tool="null_sc", sync_to_thread=False)
    def null_sc() -> MCPToolResult:
        return MCPToolResult(content="ok", structured_content=None)

    app = Litestar(route_handlers=[plain_dict, null_sc], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        plain_res = mcp_post(client, "tools/call", {"name": "plain_dict", "arguments": {}}).json()["result"]
        null_res = mcp_post(client, "tools/call", {"name": "null_sc", "arguments": {}}).json()["result"]

    assert plain_res["isError"] is False
    assert "structuredContent" not in plain_res
    assert json.loads(plain_res["content"][0]["text"]) == {"ok": True}
    assert null_res["isError"] is False
    assert "structuredContent" in null_res
    assert null_res["structuredContent"] is None


def _legacy_initialize(client: TestClient[Any]) -> dict[str, Any]:
    resp = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "route-test", "version": "1.0.0"},
            },
        },
    )
    result: dict[str, Any] = resp.json()["result"]
    return result


def _ui_app_default_mime() -> Litestar:
    @get("/a", mcp_resource="a", mcp_resource_uri="ui://app/a.html", sync_to_thread=False)
    def ui_a() -> str:
        return APP_HTML

    return Litestar(route_handlers=[ui_a], plugins=[LitestarMCP()])


def _ui_app_media_type_html() -> Litestar:
    @get("/b", media_type="text/html", mcp_resource="b", mcp_resource_uri="ui://app/b.html", sync_to_thread=False)
    def ui_b() -> str:
        return APP_HTML

    return Litestar(route_handlers=[ui_b], plugins=[LitestarMCP()])


def _ui_app_meta_non_ui_scheme() -> Litestar:
    @get(
        "/m",
        mcp_resource="m",
        mcp_resource_uri="litestar://meta_ui",
        mcp_resource_meta={"ui": {"prefersBorder": True}},
        sync_to_thread=False,
    )
    def meta_ui() -> str:
        return APP_HTML

    return Litestar(route_handlers=[meta_ui], plugins=[LitestarMCP()])


def _ui_app_templated() -> Litestar:
    @get("/t/{item_id:str}", mcp_resource="t", mcp_resource_template="ui://tpl/{item_id}.html", sync_to_thread=False)
    def tpl_ui(item_id: str) -> str:
        return f"<main>{item_id}</main>"

    return Litestar(route_handlers=[tpl_ui], plugins=[LitestarMCP()])


def _ui_app_excluded_handler_tags() -> Litestar:
    @get("/c", tags=["secret"], mcp_resource="c", mcp_resource_uri="ui://app/c.html", sync_to_thread=False)
    def secret_ui() -> str:
        return APP_HTML

    return Litestar(route_handlers=[secret_ui], plugins=[LitestarMCP(MCPConfig(exclude_tags=["secret"]))])


def _ui_app_excluded_router_tags() -> Litestar:
    @get("/d", mcp_resource="d", mcp_resource_uri="ui://app/d.html", sync_to_thread=False)
    def router_secret_ui() -> str:
        return APP_HTML

    router = Router(path="/hidden", tags=["secret"], route_handlers=[router_secret_ui])
    return Litestar(route_handlers=[router], plugins=[LitestarMCP(MCPConfig(exclude_tags=["secret"]))])


def _ui_app_disabled_override() -> Litestar:
    @get("/e", mcp_resource="e", mcp_resource_uri="ui://app/e.html", sync_to_thread=False)
    def disabled_ui() -> str:
        return APP_HTML

    return Litestar(route_handlers=[disabled_ui], plugins=[LitestarMCP(MCPConfig(ui_mime_types=[]))])


def _ui_app_explicit_override() -> Litestar:
    return Litestar(plugins=[LitestarMCP(MCPConfig(ui_mime_types=[UI_MIME_TYPE, "text/html"]))])


def _ui_app_plain_resource() -> Litestar:
    @get("/p", mcp_resource="p", mcp_resource_uri="litestar://plain", sync_to_thread=False)
    def plain_res() -> dict[str, str]:
        return {"ok": "yes"}

    return Litestar(route_handlers=[plain_res], plugins=[LitestarMCP()])


_UI_CAPABILITY_BUILDERS: dict[str, Callable[[], Litestar]] = {
    "ui_default_mime": _ui_app_default_mime,
    "ui_media_type_html": _ui_app_media_type_html,
    "meta_ui_non_ui_scheme": _ui_app_meta_non_ui_scheme,
    "templated_ui": _ui_app_templated,
    "excluded_ui_handler_tags": _ui_app_excluded_handler_tags,
    "excluded_ui_router_tags": _ui_app_excluded_router_tags,
    "disabled_override": _ui_app_disabled_override,
    "explicit_override_without_ui_routes": _ui_app_explicit_override,
    "plain_resource_only": _ui_app_plain_resource,
}


def _build_ui_capability_app(scenario: str) -> Litestar:
    return _UI_CAPABILITY_BUILDERS[scenario]()


@pytest.mark.parametrize(
    ("scenario", "expected_ui"),
    [
        ("ui_default_mime", {"mimeTypes": [UI_MIME_TYPE]}),
        ("ui_media_type_html", {"mimeTypes": ["text/html"]}),
        ("meta_ui_non_ui_scheme", {"mimeTypes": [UI_MIME_TYPE]}),
        ("templated_ui", {"mimeTypes": [UI_MIME_TYPE]}),
        ("excluded_ui_handler_tags", None),
        ("excluded_ui_router_tags", None),
        ("disabled_override", None),
        ("explicit_override_without_ui_routes", {"mimeTypes": [UI_MIME_TYPE, "text/html"]}),
        ("plain_resource_only", None),
    ],
)
def test_routes_server_discover_and_initialize_gate_ui_extension_capability(
    scenario: str,
    expected_ui: dict[str, Any] | None,
) -> None:
    app = _build_ui_capability_app(scenario)
    with TestClient(app=app) as client:
        discover_caps = mcp_post(client, "server/discover").json()["result"]["capabilities"]
        init_caps = _legacy_initialize(client)["capabilities"]

    if expected_ui is None:
        assert UI_EXTENSION not in discover_caps.get("extensions", {})
        assert UI_EXTENSION not in init_caps.get("extensions", {})
        assert "extensions" not in discover_caps
        assert "extensions" not in init_caps
    else:
        assert discover_caps["extensions"][UI_EXTENSION] == expected_ui
        assert init_caps["extensions"][UI_EXTENSION] == expected_ui


@pytest.mark.parametrize(
    ("client_caps", "explicit_ui", "expected"),
    [
        (None, None, None),
        ({}, None, None),
        ({"extensions": {}}, None, None),
        ({"extensions": {UI_EXTENSION: "invalid"}}, None, None),
        ({"extensions": {UI_EXTENSION: {"mimeTypes": [UI_MIME_TYPE]}}}, None, {"mimeTypes": [UI_MIME_TYPE]}),
        (
            {"extensions": {UI_EXTENSION: {"mimeTypes": ["text/html"]}}},
            {"mimeTypes": [UI_MIME_TYPE]},
            {"mimeTypes": [UI_MIME_TYPE]},
        ),
    ],
    ids=["none", "empty_caps", "empty_extensions", "non_mapping_ui", "negotiated_ui", "explicit_override"],
)
def test_mcp_request_context_extracts_ui_capabilities(
    client_caps: dict[str, Any] | None,
    explicit_ui: dict[str, Any] | None,
    expected: dict[str, Any] | None,
) -> None:
    caps_copy = json.loads(json.dumps(client_caps)) if client_caps is not None else None
    ctx = MCPRequestContext(
        client_id="client-1",
        owner_id=None,
        client_capabilities=caps_copy,
        ui_capabilities=explicit_ui,
    )
    if isinstance(caps_copy, dict) and isinstance(caps_copy.get("extensions", {}).get(UI_EXTENSION), dict):
        caps_copy["extensions"][UI_EXTENSION]["mimeTypes"].append("mutated")
    assert ctx.ui_capabilities == expected


def test_routes_tool_handler_reads_live_ui_capabilities() -> None:
    @get("/ui-check", mcp_tool="ui_check", mcp_output_schema=True, sync_to_thread=False)
    def ui_check() -> dict[str, Any]:
        ctx = get_mcp_request_context()
        return {"ui": ctx.ui_capabilities if ctx is not None else None}

    app = Litestar(route_handlers=[ui_check], plugins=[LitestarMCP()])
    ui_meta = {
        "io.modelcontextprotocol/clientCapabilities": {
            "extensions": {UI_EXTENSION: {"mimeTypes": [UI_MIME_TYPE]}},
        }
    }
    with TestClient(app=app) as client:
        plain_call = mcp_post(client, "tools/call", {"name": "ui_check", "arguments": {}}).json()["result"]
        ui_call = mcp_post(client, "tools/call", {"name": "ui_check", "arguments": {}, "_meta": ui_meta}).json()[
            "result"
        ]

    assert plain_call["structuredContent"] == {"ui": None}
    assert ui_call["structuredContent"] == {"ui": {"mimeTypes": [UI_MIME_TYPE]}}


@pytest.mark.parametrize("populated", [True, False], ids=["populated", "empty_omitted"])
def test_routes_emit_and_omit_descriptor_fields_across_lists(populated: bool) -> None:
    raw_title = "  Display Title  " if populated else "   "
    raw_icons: list[dict[str, Any]] = [{"src": "https://example.com/icon.svg"}] if populated else []
    raw_meta: dict[str, Any] = {"ui": {"prefersBorder": True}} if populated else {}

    @get(
        "/desc-tool",
        mcp_tool="desc_tool",
        mcp_tool_title=raw_title,
        mcp_tool_icons=raw_icons,
        mcp_tool_meta=raw_meta,
        sync_to_thread=False,
    )
    def desc_tool() -> dict[str, str]:
        return {"ok": "1"}

    @get(
        "/desc-res",
        mcp_resource="desc_res",
        mcp_resource_uri="ui://desc/app.html",
        mcp_resource_mime_type=UI_MIME_TYPE,
        mcp_resource_title=raw_title,
        mcp_resource_icons=raw_icons,
        mcp_resource_meta=raw_meta,
        sync_to_thread=False,
    )
    def desc_res() -> str:
        return APP_HTML

    @get(
        "/desc-tpl/{slug:str}",
        mcp_resource="desc_tpl",
        mcp_resource_template="ui://desc/{slug}.html",
        mcp_resource_mime_type=UI_MIME_TYPE,
        mcp_resource_title=raw_title,
        mcp_resource_icons=raw_icons,
        mcp_resource_meta=raw_meta,
        sync_to_thread=False,
    )
    def desc_tpl(slug: str) -> str:
        return f"<p>{slug}</p>"

    app = Litestar(route_handlers=[desc_tool, desc_res, desc_tpl], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        tool_entry = next(
            item for item in mcp_post(client, "tools/list").json()["result"]["tools"] if item["name"] == "desc_tool"
        )
        res_entry = next(
            item
            for item in mcp_post(client, "resources/list").json()["result"]["resources"]
            if item["name"] == "desc_res"
        )
        tpl_entry = next(
            item
            for item in mcp_post(client, "resources/templates/list").json()["result"]["resourceTemplates"]
            if item["name"] == "desc_tpl"
        )

    expected_icons = [{"src": "https://example.com/icon.svg"}]
    expected_meta = {"ui": {"prefersBorder": True}}
    if populated:
        assert tool_entry["title"] == "Display Title"
        assert tool_entry["icons"] == expected_icons
        assert tool_entry["_meta"] == expected_meta
        assert res_entry["title"] == "Display Title"
        assert res_entry["icons"] == expected_icons
        assert res_entry["_meta"] == expected_meta
        assert tpl_entry["title"] == "Display Title"
        assert tpl_entry["icons"] == expected_icons
        assert tpl_entry["_meta"] == expected_meta
    else:
        assert {"title", "icons", "_meta"}.isdisjoint(tool_entry.keys())
        assert {"title", "icons", "_meta"}.isdisjoint(res_entry.keys())
        assert {"title", "icons", "_meta"}.isdisjoint(tpl_entry.keys())


@pytest.mark.parametrize(
    ("is_template", "with_meta"),
    [
        (False, True),
        (True, True),
        (False, False),
        (True, False),
    ],
    ids=["static_with_meta", "template_with_meta", "static_without_meta", "template_without_meta"],
)
def test_resources_read_forwards_resource_meta_onto_content_entry(
    is_template: bool,
    with_meta: bool,
) -> None:
    expected_meta = {
        "ui": {
            "csp": {"connectDomains": ["https://api.example.com"]},
            "prefersBorder": True,
        }
    }
    meta_arg = expected_meta if with_meta else None

    if is_template:

        @mcp_resource(
            "demo_view",
            uri="ui://demo/{view}.html",
            mime_type=UI_MIME_TYPE,
            meta=meta_arg,
        )
        @get("/ui-meta/{view:str}", sync_to_thread=False)
        def tpl_handler(view: str) -> str:
            return f"<main>{view}</main>"

        target: Any = tpl_handler
        read_uri = "ui://demo/inspector.html"
        expected_text = "<main>inspector</main>"
    else:

        @mcp_resource(
            "demo_app",
            uri="ui://demo/app.html",
            mime_type=UI_MIME_TYPE,
            meta=meta_arg,
        )
        @get("/ui-meta-static", sync_to_thread=False)
        def static_handler() -> str:
            return APP_HTML

        target = static_handler
        read_uri = "ui://demo/app.html"
        expected_text = APP_HTML

    app = Litestar(route_handlers=[target], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        contents = mcp_post(client, "resources/read", {"uri": read_uri}).json()["result"]["contents"]

    assert len(contents) == 1
    assert contents[0]["uri"] == read_uri
    assert contents[0]["mimeType"] == UI_MIME_TYPE
    assert contents[0]["text"] == expected_text
    if with_meta:
        assert contents[0]["_meta"] == expected_meta
    else:
        assert "_meta" not in contents[0]


def test_dual_role_stacked_tool_and_ui_resource_roundtrip() -> None:
    tool_icons = [{"src": "https://example.com/widget-tool.svg"}]
    res_icons = [{"src": "https://example.com/widget-ui.svg"}]

    @mcp_tool(
        "widget_tool",
        title="Widget Tool",
        icons=tool_icons,
        output_schema=True,
        meta={"ui": {"resourceUri": "ui://widget/view.html"}},
    )
    @mcp_resource(
        "widget_ui",
        uri="ui://widget/view.html",
        title="Widget UI",
        mime_type=UI_MIME_TYPE,
        icons=res_icons,
        meta={"ui": {"prefersBorder": True}},
    )
    @get("/widget", sync_to_thread=False)
    def widget_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    app = Litestar(route_handlers=[widget_endpoint], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        discover = mcp_post(client, "server/discover").json()["result"]
        tools = {item["name"]: item for item in mcp_post(client, "tools/list").json()["result"]["tools"]}
        called = mcp_post(client, "tools/call", {"name": "widget_tool", "arguments": {}}).json()["result"]
        resources = {item["name"]: item for item in mcp_post(client, "resources/list").json()["result"]["resources"]}
        read_res = mcp_post(client, "resources/read", {"uri": "ui://widget/view.html"}).json()["result"]

    assert discover["capabilities"]["extensions"][UI_EXTENSION] == {"mimeTypes": [UI_MIME_TYPE]}

    tool_entry = tools["widget_tool"]
    assert tool_entry["title"] == "Widget Tool"
    assert tool_entry["icons"] == tool_icons
    assert tool_entry["outputSchema"]["type"] == "object"
    assert tool_entry["_meta"] == {"ui": {"resourceUri": "ui://widget/view.html"}}
    assert called["isError"] is False
    assert called["structuredContent"] == {"status": "ok"}

    res_entry = resources["widget_ui"]
    assert res_entry["uri"] == "ui://widget/view.html"
    assert res_entry["mimeType"] == UI_MIME_TYPE
    assert res_entry["title"] == "Widget UI"
    assert res_entry["icons"] == res_icons
    assert res_entry["_meta"] == {"ui": {"prefersBorder": True}}

    assert read_res["contents"][0]["uri"] == "ui://widget/view.html"
    assert read_res["contents"][0]["_meta"] == {"ui": {"prefersBorder": True}}
    assert json.loads(read_res["contents"][0]["text"]) == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_level", ["bad", "", "WARN", 7])
async def test_mcp_request_context_log_validation_and_threshold_gating(bad_level: Any) -> None:
    noop_ctx = MCPRequestContext(client_id="unit", owner_id=None)
    await noop_ctx.log("warning", {"detail": 1}, "svc")

    with pytest.raises(ValueError, match="Invalid MCP log level"):
        await noop_ctx.log(bad_level, "payload")

    recorded: list[tuple[str, Any, str | None]] = []

    async def _recorder(level: str, data: Any, logger_name: str | None) -> None:
        recorded.append((level, data, logger_name))

    gated_ctx = MCPRequestContext(client_id="unit", owner_id=None, log_level="error", log_reporter=_recorder)
    await gated_ctx.log("warning", "dropped", "auth")
    await gated_ctx.log("critical", {"ok": True}, "auth")
    assert recorded == [("critical", {"ok": True}, "auth")]


@pytest.mark.parametrize(
    ("level", "use_progress", "expect_error"),
    [
        ("error", False, False),
        ("error", True, False),
        ("bad", False, True),
        ("bad", True, True),
    ],
)
def test_routes_ctx_log_when_logging_disabled(level: str, use_progress: bool, expect_error: bool) -> None:
    @get("/log-disabled", mcp_tool="log_disabled")
    async def log_disabled(log_level: str) -> dict[str, bool]:
        await get_mcp_request_context().log(log_level, {"ok": True}, "api")
        return {"logged": True}

    app = Litestar(route_handlers=[log_disabled], plugins=[LitestarMCP(MCPConfig(logging=False))])
    call_params: dict[str, Any] = {"name": "log_disabled", "arguments": {"log_level": level}}
    if use_progress:
        call_params["_meta"] = {"progressToken": "tok-disabled"}

    with TestClient(app=app) as client:
        if use_progress:
            body, headers = mcp_envelope("tools/call", call_params, msg_id="disabled-sse")
            with client.stream("POST", "/mcp", json=body, headers=headers) as resp:
                assert resp.status_code == 200
                frames = [
                    from_json(line.removeprefix("data:").strip())
                    for line in resp.iter_lines()
                    if line.startswith("data:")
                ]
            assert all(frame.get("method") != "notifications/message" for frame in frames)
            result = frames[-1]["result"]
        else:
            resp = mcp_post(client, "tools/call", call_params)
            assert resp.status_code == 200
            result = resp.json()["result"]

    assert result["isError"] is expect_error
    if expect_error:
        assert "Invalid MCP log level" in result["content"][0]["text"]
    else:
        assert json.loads(result["content"][0]["text"]) == {"logged": True}


def test_routes_ctx_log_positional_and_keyword_logger() -> None:
    @get("/log-styles", mcp_tool="log_styles")
    async def log_styles() -> dict[str, str]:
        ctx = get_mcp_request_context()
        await ctx.log("info", "plain")
        await ctx.log("warning", "pos-msg", "api")
        await ctx.log("error", {"code": 42}, logger="db")
        return {"status": "ok"}

    app = Litestar(route_handlers=[log_styles], plugins=[LitestarMCP()])
    body, headers = mcp_envelope(
        "tools/call",
        {"name": "log_styles", "arguments": {}, "_meta": {"progressToken": "tok-log"}},
        msg_id="log-styles-1",
    )
    with TestClient(app=app) as client, client.stream("POST", "/mcp", json=body, headers=headers) as resp:
        assert resp.status_code == 200
        frames = [
            from_json(line.removeprefix("data:").strip()) for line in resp.iter_lines() if line.startswith("data:")
        ]

    log_params = [frame["params"] for frame in frames if frame.get("method") == "notifications/message"]
    assert log_params == [
        {"level": "info", "data": "plain"},
        {"level": "warning", "data": "pos-msg", "logger": "api"},
        {"level": "error", "data": {"code": 42}, "logger": "db"},
    ]
    assert frames[-1]["id"] == "log-styles-1"
    assert frames[-1]["result"]["isError"] is False


@pytest.mark.parametrize(
    ("filter_enabled", "protocol", "ui_capable", "expected_names", "expect_private_cache"),
    [
        (False, "2026-07-28", False, {"public_op", "app_only_op"}, False),
        (True, "2026-07-28", False, {"public_op"}, True),
        (True, "2026-07-28", True, {"public_op", "app_only_op"}, True),
        (True, "2025-11-25", False, {"public_op"}, True),
        (True, "2025-11-25", True, {"public_op", "app_only_op"}, True),
    ],
)
def test_filter_app_only_tools_visibility_cache_scope_and_direct_call(
    filter_enabled: bool,
    protocol: str,
    ui_capable: bool,
    expected_names: set[str],
    expect_private_cache: bool,
) -> None:
    """Verify filter_app_only_tools omits app-only tools for non-UI clients while keeping tools/call runnable."""

    @mcp_tool("public_op")
    @get("/public-op", sync_to_thread=False)
    def public_op() -> dict[str, str]:
        return {"kind": "public"}

    @mcp_tool("app_only_op", meta={"ui": {"resourceUri": "ui://app/panel.html", "visibility": ["app"]}})
    @get("/app-only-op", sync_to_thread=False)
    def app_only_op() -> dict[str, str]:
        return {"kind": "app"}

    app = Litestar(
        route_handlers=[public_op, app_only_op],
        plugins=[LitestarMCP(MCPConfig(cache_scope="public", filter_app_only_tools=filter_enabled))],
    )
    caps: dict[str, Any] = {"extensions": {UI_EXTENSION: {"mimeTypes": [UI_MIME_TYPE]}}} if ui_capable else {}

    with TestClient(app=app) as client:
        if protocol == "2026-07-28":
            body, headers = mcp_envelope(
                "tools/list",
                {"_meta": {"io.modelcontextprotocol/clientCapabilities": caps}},
            )
            list_resp = client.post("/mcp", json=body, headers=headers).json()["result"]
        else:
            init_resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25", "capabilities": caps},
                },
            )
            sid = init_resp.headers["mcp-session-id"]
            list_resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
            ).json()["result"]
        call_resp = mcp_post(client, "tools/call", {"name": "app_only_op", "arguments": {}}).json()["result"]

    assert (list_resp.get("cacheScope") == "private") is expect_private_cache
    assert {item["name"] for item in list_resp["tools"]} == expected_names
    assert call_resp["isError"] is False
    assert json.loads(call_resp["content"][0]["text"]) == {"kind": "app"}


@pytest.mark.parametrize(
    ("mode", "expect_ui_cap"),
    [
        ("template", True),
        ("static_skill", True),
        ("tool_ui", True),
        ("tool_ui_none", False),
    ],
)
def test_auto_detected_ui_server_capability_sources(tmp_path: Any, mode: str, expect_ui_cap: bool) -> None:
    """Verify server extensions UI capability detects templates, static skills, and valid tool _meta.ui."""
    handlers: list[Any] = []
    config = MCPConfig()
    if mode == "template":

        @mcp_resource("panel_tpl", uri_template="ui://panels/{slug}", mime_type=UI_MIME_TYPE)
        @get("/panels/{slug:str}", sync_to_thread=False)
        def panel_tpl(slug: str) -> Response[str]:
            return Response(content=f"<p>{slug}</p>", media_type=UI_MIME_TYPE)

        handlers.append(panel_tpl)
    elif mode == "static_skill":
        skill_dir = tmp_path / "ui-helper"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ui-helper\ndescription: UI skill\n_meta:\n  ui:\n    prefersBorder: true\n---\n\n# UI\n",
            encoding="utf-8",
        )
        config = MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path]))
    elif mode == "tool_ui":

        @mcp_tool("widget_op", meta={"ui": {"resourceUri": "ui://ext/widget.html"}})
        @get("/widget-op", sync_to_thread=False)
        def widget_op() -> dict[str, bool]:
            return {"ok": True}

        handlers.append(widget_op)
    else:

        @mcp_tool("null_ui_op", meta={"ui": None})
        @get("/null-ui-op", sync_to_thread=False)
        def null_ui_op() -> dict[str, bool]:
            return {"ok": True}

        handlers.append(null_ui_op)

    app = Litestar(route_handlers=handlers, plugins=[LitestarMCP(config)])
    with TestClient(app=app) as client:
        caps = mcp_post(client, "server/discover", {}).json()["result"]["capabilities"]

    extensions = caps.get("extensions") or {}
    assert (UI_EXTENSION in extensions) is expect_ui_cap


class _OpaqueReceipt:
    def __init__(self, code: str) -> None:
        self.code = code


class _PydanticWithOpaqueField(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    receipt: _OpaqueReceipt


@pytest.mark.parametrize("return_mode", ["custom_class", "pydantic_arbitrary"])
def test_auto_output_schema_omits_schema_and_succeeds_for_type_encoded_returns(return_mode: str) -> None:
    """Verify non-JSON-Schema return types serialized via type_encoders omit outputSchema and succeed on tools/call."""
    if return_mode == "custom_class":

        @mcp_tool("emit_encoded", output_schema=True)
        @get("/emit-encoded", sync_to_thread=False)
        def emit_custom() -> _OpaqueReceipt:
            return _OpaqueReceipt("rcpt-1")

        handler: Any = emit_custom
        expected_payload: dict[str, Any] = {"code": "rcpt-1"}
    else:

        @mcp_tool("emit_encoded", output_schema=True)
        @get("/emit-encoded", sync_to_thread=False)
        def emit_pydantic() -> _PydanticWithOpaqueField:
            return _PydanticWithOpaqueField(receipt=_OpaqueReceipt("rcpt-2"))

        handler = emit_pydantic
        expected_payload = {"receipt": {"code": "rcpt-2"}}

    def _encode_model(value: _PydanticWithOpaqueField) -> dict[str, Any]:
        return {"receipt": {"code": value.receipt.code}}

    app = Litestar(
        route_handlers=[handler],
        type_encoders={
            _OpaqueReceipt: lambda r: {"code": r.code},
            _PydanticWithOpaqueField: _encode_model,
        },
        plugins=[LitestarMCP(MCPConfig(auto_output_schema=True))],
    )
    with TestClient(app=app) as client:
        listed = mcp_post(client, "tools/list", {}).json()["result"]["tools"]
        entry = next(item for item in listed if item["name"] == "emit_encoded")
        assert "outputSchema" not in entry

        call_json = mcp_post(client, "tools/call", {"name": "emit_encoded", "arguments": {}}).json()
        assert "error" not in call_json
        result = call_json["result"]
        assert result["isError"] is False
        assert from_json(result["content"][0]["text"]) == expected_payload
