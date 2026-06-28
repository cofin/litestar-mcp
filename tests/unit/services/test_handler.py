"""Unit tests for MCPHandlerService in isolation."""

from typing import Any

import pytest
from litestar import Litestar, get

from litestar_mcp.config import MCPConfig
from litestar_mcp.jsonrpc import INVALID_PARAMS, JSONRPCErrorException
from litestar_mcp.registry import PromptRegistration
from litestar_mcp.services.handler import MCPHandlerService, RequestContext
from litestar_mcp.tasks import InMemoryTaskStore

# Using unit marker for these tests
pytestmark = pytest.mark.unit


@pytest.fixture
def dummy_app() -> Litestar:
    """A dummy Litestar application for testing."""
    return Litestar(route_handlers=[])


@pytest.fixture
def request_context() -> RequestContext:
    """Mock RequestContext."""
    return RequestContext(
        client_id="test-client-id",
        owner_id="test-owner-id",
        request=None,  # Or a Mock request if needed
    )


@pytest.fixture
def basic_config() -> MCPConfig:
    return MCPConfig(name="Test Server")


@pytest.mark.asyncio
async def test_initialize_returns_capabilities(
    dummy_app: Litestar, request_context: RequestContext, basic_config: MCPConfig
) -> None:
    service = MCPHandlerService(
        config=basic_config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
    )

    result = await service.initialize({}, request_context)
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "Test Server"
    assert "tools" in result["capabilities"]
    assert "resources" in result["capabilities"]
    assert "prompts" not in result["capabilities"]  # No prompts registered


@pytest.mark.asyncio
async def test_initialize_returns_configured_instructions(dummy_app: Litestar, request_context: RequestContext) -> None:
    service = MCPHandlerService(
        config=MCPConfig(name="Test Server", instructions="Use the audited workflow."),
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=dummy_app,
        registry=None,
    )

    result = await service.initialize({}, request_context)

    assert result["instructions"] == "Use the audited workflow."


@pytest.mark.asyncio
async def test_initialize_with_prompts_and_tasks(dummy_app: Litestar, request_context: RequestContext) -> None:
    config = MCPConfig(name="Test Server", tasks=True)
    prompt_reg = PromptRegistration(name="test_prompt", fn=lambda: "hello")
    service = MCPHandlerService(
        config=config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={"test_prompt": prompt_reg},
        app_ref=dummy_app,
        registry=None,
        task_store=InMemoryTaskStore(),
    )

    result = await service.initialize({}, request_context)
    assert "prompts" in result["capabilities"]
    assert "tasks" in result["capabilities"]


@pytest.mark.asyncio
async def test_tools_list(dummy_app: Litestar, request_context: RequestContext, basic_config: MCPConfig) -> None:
    # Set up a real handler for schema generation tests
    @get("/tool1", sync_to_thread=False)
    def tool_one(param1: str, param2: int = 1) -> str:
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
async def test_tools_list_pagination(dummy_app: Litestar, request_context: RequestContext) -> None:
    @get("/t1", sync_to_thread=False)
    def t1() -> str:
        return ""

    @get("/t2", sync_to_thread=False)
    def t2() -> str:
        return ""

    app = Litestar(route_handlers=[t1, t2])
    # The routes will be separate
    {
        route.route_handlers[0].opt.get("mcp_tool", f"tool_{i}"): route.route_handlers[0]  # type: ignore[union-attr]
        for i, route in enumerate(app.routes)
    }

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

    # First page
    result = await service.tools_list({}, request_context)
    assert len(result["tools"]) == 1
    assert "nextCursor" in result
    cursor = result["nextCursor"]

    # Second page
    result2 = await service.tools_list({"cursor": cursor}, request_context)
    assert len(result2["tools"]) == 1
    assert "nextCursor" not in result2


@pytest.mark.asyncio
async def test_tools_call_success(
    dummy_app: Litestar, request_context: RequestContext, basic_config: MCPConfig
) -> None:
    called_with: dict[str, Any] = {}

    @get("/tool", sync_to_thread=False)
    def my_tool(x: str, y: int) -> dict[str, Any]:
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
    dummy_app: Litestar, request_context: RequestContext, basic_config: MCPConfig
) -> None:
    @get("/tool", sync_to_thread=False)
    def my_tool(x: int) -> str:
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
    dummy_app: Litestar, request_context: RequestContext, basic_config: MCPConfig
) -> None:
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
    dummy_app: Litestar, request_context: RequestContext, basic_config: MCPConfig
) -> None:
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
    dummy_app: Litestar, request_context: RequestContext, basic_config: MCPConfig
) -> None:
    def dummy_prompt_fn(arg1: str) -> str:
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
async def test_resources_list_and_read(request_context: RequestContext, basic_config: MCPConfig) -> None:
    @get("/resource1", sync_to_thread=False)
    def resource_one() -> dict[str, str]:
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
    assert exc_info.value.error.code == -32002  # Resource not found
