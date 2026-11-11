"""Unit tests for Tool, tool decorator, ToolContext, and execute_tool_calls."""

import asyncio
from typing import Any, Literal

import msgspec
import pytest

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.tools import (
    Tool,
    ToolArgumentError,
    ToolCall,
    current_tool_context,
    execute_tool_calls,
    tool,
)
from litestar_mcp.mcp.content import MCPToolResult
from litestar_mcp.mcp.tool_handlers import build_controller_tool_handler, build_tool_route_handler
from litestar_mcp.utils import get_mcp_metadata


class ItemOutput(msgspec.Struct, rename="camel"):
    item_id: str
    total_count: int = 1


class ToolFailureError(RuntimeError):
    """Raised intentionally by failing tool during tests."""

    def __init__(self) -> None:
        super().__init__("tool failure")


def test_tool_decorator_attributes() -> None:
    """Verify tool decorator metadata extraction and signature inspection."""

    @tool(name="add_numbers", description="Add two numbers together")
    def add(a: int, b: int = 1) -> int:
        """Add two integers."""
        return a + b

    assert isinstance(add, Tool)
    assert add.name == "add_numbers"
    assert add.description == "Add two numbers together"
    assert "a" in add.parameters["properties"]
    assert "b" in add.parameters["properties"]
    assert add.parameters["required"] == ["a"]


def test_tool_strips_injected_parameters_from_schema() -> None:
    """Verify context and request parameters are stripped from public schema."""

    @tool
    def search(
        query: str,
        ctx: ToolContext,
        category: Literal["books", "movies"] = "books",
    ) -> list[str]:
        """Search items with context."""
        return [f"{query}:{category}:{ctx.tenant_id}"]

    assert "ctx" not in search.parameters["properties"]
    assert "query" in search.parameters["properties"]
    assert "category" in search.parameters["properties"]
    assert search.parameters["required"] == ["query"]
    assert "ctx" in search.stripped_parameters


@pytest.mark.anyio
async def test_tool_execute_with_validation() -> None:
    """Verify tool argument validation and execution with msgspec."""

    @tool
    async def multiply(x: int, y: int) -> int:
        """Multiply two numbers."""
        return x * y

    result = await multiply.execute({"x": 3, "y": 4})
    assert result == 12

    with pytest.raises(ToolArgumentError) as exc_info:
        await multiply.execute({"x": "not-an-int", "y": 4})
    assert "Failed to validate arguments" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_current_context_injection() -> None:
    """Verify current_tool_context access during tool execution."""

    @tool
    async def check_tenant() -> str:
        """Return the active tenant ID."""
        ctx = current_tool_context()
        return ctx.tenant_id if ctx and ctx.tenant_id is not None else "none"

    ctx = ToolContext(tenant_id="tenant-42")
    res = await check_tenant.execute({}, context=ctx)
    assert res == "tenant-42"

    assert current_tool_context() is None


@pytest.mark.anyio
async def test_execute_tool_calls_success_and_error() -> None:
    """Verify execute_tool_calls executes in parallel and isolates errors."""

    @tool
    async def fast_tool(val: str) -> str:
        """Return uppercased value."""
        return val.upper()

    @tool
    async def slow_tool(delay: float) -> str:
        """Wait and return ok."""
        await asyncio.sleep(delay)
        return "done"

    @tool
    async def fail_tool() -> None:
        """Raise an intentional error."""
        raise ToolFailureError

    tools_map = {
        "fast": fast_tool,
        "slow": slow_tool,
        "fail": fail_tool,
    }

    calls = [
        ToolCall(name="fast", call_id="c1", arguments={"val": "hello"}),
        ToolCall(name="slow", call_id="c2", arguments={"delay": 0.01}),
        ToolCall(name="fail", call_id="c3", arguments={}),
        ToolCall(name="unknown", call_id="c4", arguments={}),
    ]

    results = await execute_tool_calls(calls, tools_map)
    assert len(results) == 4

    assert results[0].call_id == "c1"
    assert results[0].result == "HELLO"
    assert not results[0].is_error

    assert results[1].call_id == "c2"
    assert results[1].result == "done"
    assert not results[1].is_error

    assert results[2].call_id == "c3"
    assert results[2].is_error
    assert "tool failure" in (results[2].error or "")

    assert results[3].call_id == "c4"
    assert results[3].is_error
    assert "Unknown tool" in (results[3].error or "")


@pytest.mark.parametrize(
    ("output_schema_arg", "returns_object", "expected_props", "expected_policy_kind"),
    [
        (
            True,
            True,
            {"itemId": {"type": "string"}, "totalCount": {"type": "integer", "default": 1}},
            "true",
        ),
        (True, False, None, "true"),
        (
            {"type": "object", "properties": {"slug": {"type": "string"}}},
            False,
            {"slug": {"type": "string"}},
            "dict",
        ),
        (
            {
                "$ref": "#/$defs/Wrapper",
                "$defs": {"Wrapper": {"type": "object", "properties": {"ok": {"type": "boolean"}}}},
            },
            False,
            {"ok": {"type": "boolean"}},
            "dict",
        ),
        (False, True, None, "false"),
        (None, True, None, "none"),
    ],
    ids=["auto_struct", "auto_scalar_skipped", "explicit_dict", "explicit_ref_dict", "opt_out_false", "default_none"],
)
def test_tool_decorator_output_schema_modes(
    output_schema_arg: Any,
    returns_object: bool,
    expected_props: dict[str, Any] | None,
    expected_policy_kind: str,
) -> None:
    if returns_object:

        @tool(name="sample", output_schema=output_schema_arg)
        def obj_tool(item_id: str) -> ItemOutput:
            return ItemOutput(item_id=item_id)

        registered: Tool = obj_tool
    else:

        @tool(name="sample", output_schema=output_schema_arg)
        def str_tool(item_id: str) -> str:
            return item_id

        registered = str_tool

    exported = registered.to_json_schema()
    if expected_props is None:
        assert registered.output_schema is None
        assert "output_schema" not in exported
    else:
        assert registered.output_schema is not None
        assert registered.output_schema["type"] == "object"
        assert "$ref" not in registered.output_schema
        assert registered.output_schema["properties"] == expected_props
        assert exported["output_schema"] == registered.output_schema
        exported["output_schema"]["properties"]["mutated"] = {"type": "null"}
        assert "mutated" not in registered.output_schema["properties"]

    if expected_policy_kind == "true":
        assert registered.output_schema_policy is True
    elif expected_policy_kind == "false":
        assert registered.output_schema_policy is False
    elif expected_policy_kind == "none":
        assert registered.output_schema_policy is None
    else:
        policy = registered.output_schema_policy
        assert isinstance(policy, dict)
        assert policy["properties"] == expected_props
        policy["properties"]["tampered"] = {"type": "string"}
        assert registered.output_schema is not None
        assert "tampered" not in registered.output_schema["properties"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("output_schema_arg", "expect_schema", "expected_policy"),
    [
        ({"type": "object", "properties": {"id": {"type": "string"}}}, True, "dict"),
        (
            {"$ref": "#/$defs/S", "$defs": {"S": {"type": "object", "properties": {"id": {"type": "string"}}}}},
            True,
            "dict",
        ),
        (True, True, True),
        (False, False, False),
        (None, False, None),
    ],
    ids=["dict", "ref_dict", "bool_true", "bool_false", "none"],
)
async def test_tool_from_schema_and_bound_descriptor_preserve_output_schema(
    output_schema_arg: Any,
    expect_schema: bool,
    expected_policy: Any,
) -> None:
    def typed_call(item_id: str) -> ItemOutput:
        return ItemOutput(item_id=item_id)

    from_schema_tool = Tool.from_schema(
        "lookup_item",
        description="Lookup",
        parameters={"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"]},
        call=typed_call,
        output_schema=output_schema_arg,
    )

    class ItemController:
        @tool(name="bound_lookup", output_schema=output_schema_arg)
        def lookup(self, item_id: str) -> ItemOutput:
            return ItemOutput(item_id=item_id)

    bound_tool = ItemController().lookup

    if expect_schema:
        assert from_schema_tool.output_schema is not None
        assert from_schema_tool.output_schema["type"] == "object"
        assert bound_tool.output_schema is not None
        assert bound_tool.output_schema["type"] == "object"
    else:
        assert from_schema_tool.output_schema is None
        assert bound_tool.output_schema is None

    if expected_policy == "dict":
        assert isinstance(from_schema_tool.output_schema_policy, dict)
        assert isinstance(bound_tool.output_schema_policy, dict)
    else:
        assert from_schema_tool.output_schema_policy is expected_policy
        assert bound_tool.output_schema_policy is expected_policy

    bound_res = await bound_tool.execute({"item_id": "abc"})
    assert bound_res == {"itemId": "abc", "totalCount": 1}

    noop_auto = Tool.from_schema("noop_tool", output_schema=True)
    assert noop_auto.output_schema is None
    assert noop_auto.output_schema_policy is True


@pytest.mark.parametrize("factory_kind", ["decorator", "from_schema"])
@pytest.mark.parametrize(
    ("bad_schema", "exc_type", "expected_match"),
    [
        ("bad", TypeError, "output_schema must be a mapping, bool, or None"),
        (123, TypeError, "output_schema must be a mapping, bool, or None"),
        (["x"], TypeError, "output_schema must be a mapping, bool, or None"),
        ({"type": "string"}, ValueError, "output_schema mapping must resolve to root type 'object'"),
        ({}, ValueError, "root type 'object'"),
        ({"$ref": "#/$defs/Missing"}, ValueError, "root type 'object'"),
    ],
    ids=["str", "int", "list", "string_schema", "empty_dict", "dangling_ref"],
)
def test_tool_and_from_schema_reject_invalid_output_schema(
    factory_kind: str,
    bad_schema: Any,
    exc_type: type[Exception],
    expected_match: str,
) -> None:
    with pytest.raises(exc_type, match=expected_match):
        if factory_kind == "decorator":
            tool(name="bad", output_schema=bad_schema)
        else:
            Tool.from_schema("bad", output_schema=bad_schema)


@pytest.mark.parametrize("builder_kind", ["controller", "standalone"])
@pytest.mark.parametrize(
    ("policy", "expected_meta"),
    [
        (True, True),
        (False, False),
        (
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        (None, None),
    ],
    ids=["true", "false", "explicit_dict", "none"],
)
def test_tool_handlers_forward_output_schema_policy(
    builder_kind: str,
    policy: Any,
    expected_meta: Any,
) -> None:
    if builder_kind == "controller":

        class ToolHolder:
            @tool(name="policy_tool", output_schema=policy)
            def run(self, query: str) -> ItemOutput:
                return ItemOutput(item_id=query)

        handler = build_controller_tool_handler(ToolHolder.__dict__["run"])
    else:

        @tool(name="policy_tool", output_schema=policy)
        def run_standalone(query: str) -> ItemOutput:
            return ItemOutput(item_id=query)

        handler = build_tool_route_handler(run_standalone, base_path="/mcp")

    metadata = get_mcp_metadata(handler) or get_mcp_metadata(handler.fn)
    assert metadata is not None
    if expected_meta is None:
        assert "output_schema" not in metadata
    elif isinstance(expected_meta, bool):
        assert metadata["output_schema"] is expected_meta
    else:
        assert metadata["output_schema"] == expected_meta


@pytest.mark.anyio
async def test_tool_execute_passes_through_mcp_tool_result() -> None:
    @tool(name="rich_result")
    async def rich_result() -> MCPToolResult:
        return MCPToolResult(content="custom note")

    result = await rich_result.execute({})
    assert isinstance(result, MCPToolResult)
    assert result.to_result() == {"content": [{"type": "text", "text": "custom note"}], "isError": False}


def test_tool_descriptor_fields_preserved_across_decorator_bound_and_from_schema() -> None:
    raw_icons = [{"src": "https://example.com/app.svg", "mimeType": "image/svg+xml"}]
    raw_meta: dict[str, Any] = {"ui": {"resourceUri": "ui://app/panel.html", "visibility": ["app"]}}

    @tool(
        name="panel_tool",
        title="  Panel Tool  ",
        icons=raw_icons,
        meta=raw_meta,
        output_schema=True,
    )
    def open_panel(item_id: str) -> ItemOutput:
        return ItemOutput(item_id=item_id)

    class PanelSkill:
        @tool(
            name="bound_panel",
            title="Bound Panel",
            icons=raw_icons,
            meta=raw_meta,
            output_schema=True,
        )
        def show(self, item_id: str) -> ItemOutput:
            return ItemOutput(item_id=item_id)

    schema_tool = Tool.from_schema(
        "schema_panel",
        description="Schema tool",
        parameters={"type": "object", "properties": {"item_id": {"type": "string"}}},
        title="  Schema Panel  ",
        icons=raw_icons,
        meta=raw_meta,
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )

    raw_icons[0]["src"] = "tampered"
    raw_meta["ui"]["visibility"].append("model")

    bound_panel = PanelSkill().show
    assert open_panel.title == "Panel Tool"
    assert bound_panel.title == "Bound Panel"
    assert schema_tool.title == "Schema Panel"

    expected_icons = [{"src": "https://example.com/app.svg", "mimeType": "image/svg+xml"}]
    expected_meta = {"ui": {"resourceUri": "ui://app/panel.html", "visibility": ["app"]}}
    assert open_panel.icons == expected_icons
    assert bound_panel.icons == expected_icons
    assert schema_tool.icons == expected_icons
    assert open_panel.meta == expected_meta
    assert bound_panel.meta == expected_meta
    assert schema_tool.meta == expected_meta
    assert open_panel.output_schema is not None
    assert bound_panel.output_schema is not None
    assert schema_tool.output_schema == {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    exported = open_panel.to_json_schema()
    assert "title" not in exported
    assert "icons" not in exported
    assert "meta" not in exported
    assert "_meta" not in exported
    assert "output_schema" in exported


@pytest.mark.parametrize("constructor_kind", ["direct", "decorator", "bare_decorator", "from_schema", "bound"])
@pytest.mark.parametrize(
    ("title", "icons", "meta"),
    [
        (None, None, None),
        ("", [], {}),
        ("   ", [], {}),
    ],
    ids=["none", "empty", "whitespace"],
)
def test_tool_normalizes_empty_and_whitespace_descriptors_to_none(
    constructor_kind: str,
    title: str | None,
    icons: list[dict[str, Any]] | None,
    meta: dict[str, Any] | None,
) -> None:
    def plain_fn(x: int) -> int:
        return x

    if constructor_kind == "direct":
        instance = Tool(plain_fn, title=title, icons=icons, meta=meta)
    elif constructor_kind == "decorator":
        instance = tool(name="dec", title=title, icons=icons, meta=meta)(plain_fn)
    elif constructor_kind == "bare_decorator":
        instance = tool(plain_fn, title=title, icons=icons, meta=meta)
    elif constructor_kind == "from_schema":
        instance = Tool.from_schema("sch", title=title, icons=icons, meta=meta)
    else:

        class Holder:
            m = Tool(lambda _self, x: x, name="m", title=title, icons=icons, meta=meta)

        instance = Holder().m

    assert instance.title is None
    assert instance.icons is None
    assert instance.meta is None
    exported = instance.to_json_schema()
    assert "title" not in exported
    assert "icons" not in exported
    assert "meta" not in exported
