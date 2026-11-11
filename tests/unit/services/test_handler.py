"""Unit tests for MCPHandlerService in isolation."""

from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from litestar import Litestar, get

from litestar_mcp.core.jsonrpc import INTERNAL_ERROR, INVALID_PARAMS, JSONRPCErrorException
from litestar_mcp.mcp.config import MCPConfig, MCPSkillsConfig
from litestar_mcp.mcp.content import MCPInputRequiredResult, MCPToolResult
from litestar_mcp.mcp.registry import PromptRegistration
from litestar_mcp.mcp.service import SKILLS_EXTENSION, MCPHandlerService, MCPRequestContext
from litestar_mcp.mcp.skills import SkillCatalog
from litestar_mcp.mcp.tasks import MCPTaskStore
from litestar_mcp.utils import mcp_resource, mcp_tool

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


class _ServiceItem(msgspec.Struct, rename="camel"):
    item_id: str
    child_tags: list[str]


@pytest.fixture
def dummy_app() -> "Litestar":
    """A dummy Litestar application for testing."""
    return Litestar(route_handlers=[])


@pytest.fixture
def request_context() -> "MCPRequestContext":
    """Mock MCPRequestContext."""
    return MCPRequestContext(
        client_id="test-client-id",
        owner_id="test-owner-id",
        request=None,
    )


@pytest.fixture
def basic_config() -> "MCPConfig":
    return MCPConfig(name="Test Server")


@pytest.mark.asyncio
async def test_server_discover_returns_capabilities(
    dummy_app: "Litestar", request_context: "MCPRequestContext", basic_config: "MCPConfig"
) -> "None":
    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
    )

    result = await service.server_discover({}, request_context)
    assert result["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]
    assert "tools" in result["capabilities"]
    assert "resources" in result["capabilities"]
    assert "prompts" not in result["capabilities"]


@pytest.mark.asyncio
async def test_server_discover_returns_configured_instructions(
    dummy_app: "Litestar", request_context: "MCPRequestContext"
) -> "None":
    service = MCPHandlerService(
        config=MCPConfig(name="Test Server", instructions="Use the audited workflow."),
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
    )

    result = await service.server_discover({}, request_context)

    assert result["instructions"] == "Use the audited workflow."


@pytest.mark.asyncio
async def test_server_discover_with_prompts_and_tasks(
    dummy_app: "Litestar", request_context: "MCPRequestContext"
) -> "None":
    config = MCPConfig(name="Test Server", tasks=True)
    prompt_reg = PromptRegistration(name="test_prompt", fn=lambda: "hello")
    service = MCPHandlerService(
        config=config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={"test_prompt": prompt_reg},
        app_ref=dummy_app,
        registry=None,
        task_store=MCPTaskStore(),
    )

    result = await service.server_discover({}, request_context)
    assert "prompts" in result["capabilities"]
    assert result["capabilities"]["extensions"]["io.modelcontextprotocol/tasks"] == {}


@pytest.mark.asyncio
async def test_tools_list(
    dummy_app: "Litestar", request_context: "MCPRequestContext", basic_config: "MCPConfig"
) -> "None":
    # Set up a real handler for schema generation tests
    @get("/tool1", sync_to_thread=False)
    def tool_one(param1: "str", param2: "int" = 1) -> "str":
        return "result"

    # Extract the route handler from dummy app (need to register it first)
    app = Litestar(route_handlers=[tool_one])
    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]

    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={"tool_one": handler},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    result = await service.tools_list({}, request_context)
    assert len(result["tools"]) == 1
    tool = result["tools"][0]
    assert tool["name"] == "tool_one"
    assert tool["inputSchema"]["type"] == "object"
    assert "param1" in tool["inputSchema"]["properties"]
    assert "param2" in tool["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_tools_list_pagination(dummy_app: "Litestar", request_context: "MCPRequestContext") -> "None":
    @get("/t1", sync_to_thread=False)
    def t1() -> "str":
        return ""

    @get("/t2", sync_to_thread=False)
    def t2() -> "str":
        return ""

    app = Litestar(route_handlers=[t1, t2])
    config = MCPConfig(list_page_size=1)
    service = MCPHandlerService(
        config=config,
        discovered_tools={
            "tool_a": app.routes[0].route_handlers[0],  # type: ignore[union-attr]
            "tool_b": app.routes[1].route_handlers[0],  # type: ignore[union-attr]
        },
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    result = await service.tools_list({}, request_context)
    assert len(result["tools"]) == 1
    assert "nextCursor" in result
    cursor = result["nextCursor"]

    result2 = await service.tools_list({"cursor": cursor}, request_context)
    assert len(result2["tools"]) == 1
    assert "nextCursor" not in result2


@pytest.mark.asyncio
async def test_tools_call_success(
    dummy_app: "Litestar", request_context: "MCPRequestContext", basic_config: "MCPConfig"
) -> "None":
    called_with: dict[str, Any] = {}

    @get("/tool", sync_to_thread=False)
    def my_tool(x: "str", y: "int") -> "dict[str, Any]":
        called_with["x"] = x
        called_with["y"] = y
        return {"ok": True}

    app = Litestar(route_handlers=[my_tool])
    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]

    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={"my_tool": handler},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    params = {"name": "my_tool", "arguments": {"x": "hello", "y": 42}}
    result = await service.tools_call(params, request_context)

    assert result["isError"] is False
    assert "ok" in result["content"][0]["text"]
    assert called_with == {"x": "hello", "y": 42}


@pytest.mark.asyncio
async def test_tools_call_invalid_arguments(
    dummy_app: "Litestar", request_context: "MCPRequestContext", basic_config: "MCPConfig"
) -> "None":
    @get("/tool", sync_to_thread=False)
    def my_tool(x: "int") -> "str":
        return "ok"

    app = Litestar(route_handlers=[my_tool])
    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]

    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={"my_tool": handler},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    # Call with string instead of int
    params = {"name": "my_tool", "arguments": {"x": "not-an-int"}}
    result = await service.tools_call(params, request_context)

    assert result["isError"] is True
    assert "Invalid tool arguments" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tools_call_missing_name(
    dummy_app: "Litestar", request_context: "MCPRequestContext", basic_config: "MCPConfig"
) -> "None":
    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
    )
    with pytest.raises(JSONRPCErrorException) as exc_info:
        await service.tools_call({"arguments": {}}, request_context)
    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_tools_call_tool_not_found(
    dummy_app: "Litestar", request_context: "MCPRequestContext", basic_config: "MCPConfig"
) -> "None":
    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
    )
    with pytest.raises(JSONRPCErrorException) as exc_info:
        await service.tools_call({"name": "non_existent"}, request_context)
    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_prompts_list_and_get(
    dummy_app: "Litestar", request_context: "MCPRequestContext", basic_config: "MCPConfig"
) -> "None":
    def dummy_prompt_fn(arg1: "str") -> "str":
        return f"Prompt: {arg1}"

    prompt_reg = PromptRegistration(
        name="test_prompt",
        fn=dummy_prompt_fn,
        title="Test Prompt Title",
        description="Test Prompt Desc",
        arguments=[{"name": "arg1", "description": "arg 1", "required": True}],
    )

    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={"test_prompt": prompt_reg},
        app_ref=dummy_app,
        registry=None,
    )

    # Test list
    list_result = await service.prompts_list({}, request_context)
    assert len(list_result["prompts"]) == 1
    assert list_result["prompts"][0]["name"] == "test_prompt"

    # Test get success
    get_result = await service.prompts_get({"name": "test_prompt", "arguments": {"arg1": "hello"}}, request_context)
    assert "messages" in get_result
    assert get_result["messages"][0]["content"]["text"] == "Prompt: hello"

    # Test get missing required arg
    with pytest.raises(JSONRPCErrorException) as exc_info:
        await service.prompts_get({"name": "test_prompt", "arguments": {}}, request_context)
    assert exc_info.value.error.code == INVALID_PARAMS
    assert "missing a required argument" in exc_info.value.error.message

    # Test get argument type not string (must be flat string record)
    with pytest.raises(JSONRPCErrorException) as exc_info:
        await service.prompts_get({"name": "test_prompt", "arguments": {"arg1": 123}}, request_context)
    assert exc_info.value.error.code == INVALID_PARAMS
    assert "must be a string" in exc_info.value.error.message


@pytest.mark.asyncio
async def test_resources_list_and_read(request_context: "MCPRequestContext", basic_config: "MCPConfig") -> "None":
    @get("/resource1", sync_to_thread=False)
    def resource_one() -> "dict[str, str]":
        return {"data": "my-resource-data"}

    app = Litestar(route_handlers=[resource_one])
    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]

    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={},
        discovered_resources={"res_one": handler},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    # Test list (should always contain openapi)
    list_result = await service.resources_list({}, request_context)
    assert len(list_result["resources"]) == 2  # openapi + res_one
    names = {r["name"] for r in list_result["resources"]}
    assert "openapi" in names
    assert "res_one" in names

    # Test read success
    read_result = await service.resources_read({"uri": "litestar://res_one"}, request_context)
    assert len(read_result["contents"]) == 1
    assert "my-resource-data" in read_result["contents"][0]["text"]

    # Test read not found
    with pytest.raises(JSONRPCErrorException) as exc_info:
        await service.resources_read({"uri": "litestar://unknown"}, request_context)
    assert exc_info.value.error.code == -32602


@pytest.mark.asyncio
async def test_server_discover_advertises_skills_directory_read(
    dummy_app: "Litestar", request_context: "MCPRequestContext", tmp_path: "Path"
) -> "None":
    enabled_service = MCPHandlerService(
        config=MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path], directory_read=True)),
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
        skill_catalog=SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path], directory_read=True)),
    )
    enabled_result = await enabled_service.server_discover({}, request_context)
    assert enabled_result["capabilities"]["extensions"] == {
        SKILLS_EXTENSION: {"directoryRead": True, "listChanged": True},
    }

    disabled_service = MCPHandlerService(
        config=MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path], directory_read=False)),
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
        skill_catalog=SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path], directory_read=False)),
    )
    disabled_result = await disabled_service.server_discover({}, request_context)
    assert disabled_result["capabilities"]["extensions"] == {
        SKILLS_EXTENSION: {"directoryRead": False, "listChanged": True},
    }

    catalog_only_service = MCPHandlerService(
        config=MCPConfig(skills=None),
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
        skill_catalog=SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path])),
    )
    catalog_only_result = await catalog_only_service.server_discover({}, request_context)
    assert catalog_only_result["capabilities"]["extensions"] == {
        SKILLS_EXTENSION: {"directoryRead": True, "listChanged": True},
    }


@pytest.mark.asyncio
async def test_skills_get_and_directory_read_reject_missing_uri(
    dummy_app: "Litestar", request_context: "MCPRequestContext", tmp_path: "Path"
) -> "None":
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    service = MCPHandlerService(
        config=MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path])),
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
        skill_catalog=catalog,
    )

    with pytest.raises(JSONRPCErrorException) as skills_get_exc:
        await service.skills_get({}, request_context)
    assert skills_get_exc.value.error.code == INVALID_PARAMS

    with pytest.raises(JSONRPCErrorException) as directory_read_exc:
        await service.resources_directory_read({}, request_context)
    assert directory_read_exc.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_initialize_and_ping_on_handler_service(
    dummy_app: "Litestar", request_context: "MCPRequestContext"
) -> "None":
    service = MCPHandlerService(
        config=MCPConfig(name="Test Server", instructions="Service instructions."),
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
    )

    latest_res = await service.initialize({"protocolVersion": "2025-11-25"}, request_context)
    assert latest_res["protocolVersion"] == "2025-11-25"

    init_res = await service.initialize({"protocolVersion": "2025-06-18"}, request_context)
    assert init_res["protocolVersion"] == "2025-06-18"
    assert init_res["serverInfo"]["name"] == "Test Server"
    assert init_res["instructions"] == "Service instructions."

    fallback_res = await service.initialize({"protocolVersion": "2024-11-05"}, request_context)
    assert fallback_res["protocolVersion"] == "2025-11-25"

    default_res = await service.initialize({}, request_context)
    assert default_res["protocolVersion"] == "2025-11-25"

    ping_res = await service.ping({}, request_context)
    assert ping_res == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "res_name", "expected_uri", "expected_mime", "expected_snippet"),
    [
        ("decorator_uri", "dash", "ui://service/dash.html", "text/html;profile=mcp-app", "<h1>Svc</h1>"),
        ("brace_free_opt_template", "cfg", "ui://service/cfg.json", "application/json", '"enabled":true'),
    ],
)
async def test_resources_list_and_read_resolve_decorator_uri_and_brace_free_opt_template(
    request_context: "MCPRequestContext",
    basic_config: "MCPConfig",
    mode: "str",
    res_name: "str",
    expected_uri: "str",
    expected_mime: "str",
    expected_snippet: "str",
) -> "None":
    if mode == "decorator_uri":

        @mcp_resource(res_name, uri=expected_uri, mime_type=expected_mime)
        @get("/svc-dash", sync_to_thread=False)
        def svc_dash() -> "str":
            return "<h1>Svc</h1>"

        app = Litestar(route_handlers=[svc_dash])
    else:

        @get("/svc-cfg", mcp_resource=res_name, mcp_resource_template=expected_uri, sync_to_thread=False)
        def svc_cfg() -> "dict[str, bool]":
            return {"enabled": True}

        app = Litestar(route_handlers=[svc_cfg])

    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]
    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={},
        discovered_resources={res_name: handler},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    listed = await service.resources_list({}, request_context)
    entry = next(item for item in listed["resources"] if item["name"] == res_name)
    assert entry["uri"] == expected_uri
    assert entry["mimeType"] == expected_mime

    read_res = await service.resources_read({"uri": expected_uri}, request_context)
    content = read_res["contents"][0]
    assert content["uri"] == expected_uri
    assert content["mimeType"] == expected_mime
    assert expected_snippet in content["text"].replace(" ", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("opt_value", "decorator_value", "auto_enabled", "expected_props"),
    [
        (
            {"type": "object", "properties": {"fromOpt": {"type": "string"}}},
            {"type": "object", "properties": {"fromMeta": {"type": "string"}}},
            True,
            {"fromOpt": {"type": "string"}},
        ),
        (
            False,
            {"type": "object", "properties": {"fromMeta": {"type": "string"}}},
            True,
            None,
        ),
        (
            None,
            {"type": "object", "properties": {"fromMeta": {"type": "string"}}},
            True,
            {"fromMeta": {"type": "string"}},
        ),
        (None, False, True, None),
        (
            None,
            None,
            True,
            {"itemId": {"type": "string"}, "childTags": {"type": "array", "items": {"type": "string"}}},
        ),
        (None, None, False, None),
    ],
    ids=[
        "opt_overrides_metadata_and_auto",
        "opt_false_suppresses_metadata_and_auto",
        "metadata_overrides_auto",
        "metadata_false_suppresses_auto",
        "auto_synthesizes_struct",
        "default_false_omits_schema",
    ],
)
async def test_service_resolve_output_schema_precedence_and_cache_isolation(
    request_context: MCPRequestContext,
    opt_value: Any,
    decorator_value: Any,
    auto_enabled: bool,
    expected_props: dict[str, Any] | None,
) -> None:
    route_opt: dict[str, Any] = {"mcp_tool": "fetch_item"}
    if opt_value is not None:
        route_opt["mcp_output_schema"] = opt_value

    @mcp_tool("fetch_item", output_schema=decorator_value)
    @get("/svc-item", opt=route_opt, sync_to_thread=False)
    def fetch_item() -> _ServiceItem:
        return _ServiceItem(item_id="i-1", child_tags=["a"])

    app = Litestar(route_handlers=[fetch_item])
    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]
    service = MCPHandlerService(
        config=MCPConfig(name="Test Server", auto_output_schema=auto_enabled),
        discovered_tools={"fetch_item": handler},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    listed = await service.tools_list({}, request_context)
    entry = listed["tools"][0]
    if expected_props is None:
        assert "outputSchema" not in entry
    else:
        assert entry["outputSchema"]["type"] == "object"
        assert entry["outputSchema"]["properties"] == expected_props
        entry["outputSchema"]["properties"]["mutated"] = {"type": "null"}
        second_listed = await service.tools_list({}, request_context)
        assert "mutated" not in second_listed["tools"][0]["outputSchema"]["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad_opt", "expected_fragment"),
    [
        ("invalid", "invalid output_schema policy of type str"),
        (123, "invalid output_schema policy of type int"),
        (["a"], "invalid output_schema policy of type list"),
        ({"type": "string"}, "must resolve to root type 'object'"),
        ({}, "must resolve to root type 'object'"),
        ({"$ref": "#/$defs/Missing"}, "must resolve to root type 'object'"),
    ],
    ids=["str", "int", "list", "string_schema", "empty_dict", "dangling_ref"],
)
async def test_service_invalid_opt_output_schema_raises_internal_error_without_executing(
    request_context: MCPRequestContext,
    basic_config: MCPConfig,
    bad_opt: Any,
    expected_fragment: str,
) -> None:
    calls: list[str] = []

    @get("/bad-opt", opt={"mcp_tool": "bad_opt", "mcp_output_schema": bad_opt}, sync_to_thread=False)
    def bad_opt_tool() -> dict[str, str]:
        calls.append("executed")
        return {"id": "1"}

    app = Litestar(route_handlers=[bad_opt_tool])
    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]
    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={"bad_opt": handler},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    with pytest.raises(JSONRPCErrorException) as list_exc:
        await service.tools_list({}, request_context)
    assert list_exc.value.error.code == INTERNAL_ERROR
    assert "bad_opt" in list_exc.value.error.message
    assert expected_fragment in list_exc.value.error.message

    with pytest.raises(JSONRPCErrorException) as call_exc:
        await service.tools_call({"name": "bad_opt", "arguments": {}}, request_context)
    assert call_exc.value.error.code == INTERNAL_ERROR
    assert expected_fragment in call_exc.value.error.message
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("return_payload", "expected_wire"),
    [
        (
            MCPToolResult(content="custom note"),
            {"content": [{"type": "text", "text": "custom note"}], "isError": False},
        ),
        (
            MCPToolResult(content="done", structured_content={"itemId": "x", "childTags": ["t"]}),
            {
                "content": [{"type": "text", "text": "done"}],
                "structuredContent": {"itemId": "x", "childTags": ["t"]},
                "isError": False,
            },
        ),
        (
            MCPInputRequiredResult(
                input_requests={"confirm": {"prompt": "Continue?"}},
                request_state="tok-1",
            ),
            {
                "resultType": "input_required",
                "inputRequests": {"confirm": {"prompt": "Continue?"}},
                "requestState": "tok-1",
            },
        ),
    ],
    ids=["unset_structured_content", "explicit_dict_structured_content", "input_required_mid_mrtr"],
)
async def test_service_explicit_mcp_helpers_with_declared_object_output_schema(
    request_context: MCPRequestContext,
    return_payload: Any,
    expected_wire: dict[str, Any],
) -> None:
    @get(
        "/explicit",
        opt={
            "mcp_tool": "explicit_tool",
            "mcp_output_schema": {"type": "object", "properties": {"itemId": {"type": "string"}}},
        },
        sync_to_thread=False,
    )
    def explicit_tool() -> Any:
        return return_payload

    app = Litestar(route_handlers=[explicit_tool])
    handler = app.routes[0].route_handlers[0]  # type: ignore[union-attr]
    service = MCPHandlerService(
        config=MCPConfig(name="Test Server"),
        discovered_tools={"explicit_tool": handler},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=None,
    )

    result = await service.tools_call({"name": "explicit_tool", "arguments": {}}, request_context)
    assert result == expected_wire
