"""Tests for MCP decorators."""

from types import MappingProxyType
from typing import Any

import pytest
from litestar import Litestar
from litestar.handlers import get

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.core.tools import tool
from litestar_mcp.utils import (
    classify_resource_uri,
    get_all_mcp_metadata,
    get_mcp_metadata,
    mcp_prompt,
    mcp_resource,
    mcp_tool,
    render_description,
    set_mcp_metadata,
)


def test_mcp_tool_decorator_storage() -> "None":
    @mcp_tool(name="test_tool")
    @get("/", sync_to_thread=False)
    def my_handler() -> "str":
        return "hello"

    metadata = get_mcp_metadata(my_handler)
    assert metadata is not None
    assert metadata["type"] == "tool"
    assert metadata["name"] == "test_tool"


def test_mcp_resource_decorator_storage() -> "None":
    @mcp_resource(name="test_resource")
    @get("/", sync_to_thread=False)
    def my_handler() -> "str":
        return "hello"

    metadata = get_mcp_metadata(my_handler)
    assert metadata is not None
    assert metadata["type"] == "resource"
    assert metadata["name"] == "test_resource"


def test_mcp_tool_stores_description_kwargs() -> "None":
    @mcp_tool(
        "t",
        description="d",
        agent_instructions="ai",
        when_to_use="wtu",
        returns="r",
    )
    @get("/", sync_to_thread=False)
    def handler() -> "str":
        return ""

    metadata = get_mcp_metadata(handler)
    assert metadata is not None
    assert metadata["description"] == "d"
    assert metadata["agent_instructions"] == "ai"
    assert metadata["when_to_use"] == "wtu"
    assert metadata["returns"] == "r"


def test_mcp_resource_stores_description_kwargs() -> "None":
    @mcp_resource(
        "r",
        description="d",
        agent_instructions="ai",
        when_to_use="wtu",
        returns="ret",
    )
    @get("/", sync_to_thread=False)
    def handler() -> "str":
        return ""

    metadata = get_mcp_metadata(handler)
    assert metadata is not None
    assert metadata["description"] == "d"
    assert metadata["agent_instructions"] == "ai"
    assert metadata["when_to_use"] == "wtu"
    assert metadata["returns"] == "ret"


def test_mcp_resource_stores_mime_type() -> "None":
    @mcp_resource("r", mime_type="application/pdf")
    @get("/", sync_to_thread=False)
    def handler() -> "str":
        return ""

    metadata = get_mcp_metadata(handler)
    assert metadata is not None
    assert metadata["mime_type"] == "application/pdf"


def test_description_kwargs_default_to_none_and_omitted_from_metadata() -> "None":
    @mcp_tool("t")
    @get("/", sync_to_thread=False)
    def handler() -> "str":
        return ""

    metadata = get_mcp_metadata(handler)
    assert metadata is not None
    assert "description" not in metadata
    assert "agent_instructions" not in metadata
    assert "when_to_use" not in metadata
    assert "returns" not in metadata


def test_mcp_tool_read_only_and_annotations_storage() -> "None":
    @mcp_tool("ro_tool", read_only=True, annotations={"idempotentHint": True})
    @get("/ro", sync_to_thread=False)
    def ro_handler() -> "str":
        return "ok"

    metadata = get_mcp_metadata(ro_handler)
    assert metadata is not None
    assert metadata["annotations"] == {"readOnlyHint": True, "idempotentHint": True}

    @tool(name="standalone_ro", read_only=True, annotations={"openWorldHint": False})
    def standalone_fn(query: "str") -> "str":
        """Run a read-only query."""
        return query

    assert standalone_fn.annotations == {"readOnlyHint": True, "openWorldHint": False}


def test_combined_mcp_tool_decorator_and_opt_resource_discovery() -> "None":
    @mcp_tool("combined_tool")
    @get("/both", opt={"mcp_resource": "combined_res", "mcp_read_only": True}, sync_to_thread=False)
    def combined_handler() -> "dict[str, str]":
        """Serve both tool and resource."""
        return {"status": "ok"}

    plugin = LitestarMCP(MCPConfig())
    Litestar(route_handlers=[combined_handler], plugins=[plugin])

    assert "combined_tool" in plugin.discovered_tools
    assert "combined_res" in plugin.discovered_resources


def test_decorators_default_name_to_function_name() -> "None":
    @mcp_tool()
    @get("/tool", sync_to_thread=False)
    def lookup_user() -> "str":
        return "user"

    @mcp_resource()
    def app_settings() -> "dict[str, str]":
        return {}

    @mcp_prompt()
    def summarize(text: "str") -> "str":
        return text

    assert (get_mcp_metadata(lookup_user) or {})["name"] == "lookup_user"
    assert (get_mcp_metadata(app_settings) or {})["name"] == "app_settings"
    assert (get_mcp_metadata(summarize) or {})["name"] == "summarize"


def test_opt_true_registers_primitives_under_function_names() -> "None":
    @get("/users", mcp_tool=True, sync_to_thread=False)
    def list_users() -> "list[str]":
        return []

    @get("/user-schema", mcp_resource=True, sync_to_thread=False)
    def user_schema() -> "dict[str, str]":
        return {}

    @get("/review", mcp_prompt=True, sync_to_thread=False)
    def code_review() -> "str":
        return "review"

    plugin = LitestarMCP()
    Litestar(route_handlers=[list_users, user_schema, code_review], plugins=[plugin])

    assert "list_users" in plugin.discovered_tools
    assert "user_schema" in plugin.discovered_resources
    assert "code_review" in plugin.discovered_prompts


@pytest.mark.parametrize(
    ("uri", "uri_template", "expected"),
    [
        ("ui://app/dash.html", None, ("ui://app/dash.html", None)),
        (None, "ui://app/cfg.json", ("ui://app/cfg.json", None)),
        ("app://items/{item_id}", None, (None, "app://items/{item_id}")),
        (None, "app://w/{wid}", (None, "app://w/{wid}")),
        (None, None, (None, None)),
    ],
)
def test_classify_resource_uri(
    uri: "str | None",
    uri_template: "str | None",
    expected: "tuple[str | None, str | None]",
) -> "None":
    assert classify_resource_uri(uri=uri, uri_template=uri_template) == expected


@pytest.mark.parametrize(
    ("res_name", "kwargs", "expected_uri"),
    [
        ("dash", {"uri": "ui://app/dashboard.html"}, "ui://app/dashboard.html"),
        ("cfg", {"uri_template": "ui://app/cfg.json"}, "ui://app/cfg.json"),
    ],
)
def test_mcp_resource_static_uri_classification_and_discovery(
    res_name: "str",
    kwargs: "dict[str, Any]",
    expected_uri: "str",
) -> "None":
    @mcp_resource(res_name, **kwargs)
    @get("/static", sync_to_thread=False)
    def static_handler() -> "str":
        return "ok"

    metadata = get_mcp_metadata(static_handler)
    assert metadata is not None
    assert metadata["resource_uri"] == expected_uri
    assert "resource_template" not in metadata

    plugin = LitestarMCP()
    Litestar(route_handlers=[static_handler], plugins=[plugin])
    assert res_name in plugin.discovered_resources
    assert res_name not in plugin.registry.templates


@pytest.mark.parametrize(
    ("res_name", "kwargs", "expected_template"),
    [
        ("item", {"uri": "app://items/{item_id}"}, "app://items/{item_id}"),
        ("wf", {"uri_template": "app://w/{item_id}"}, "app://w/{item_id}"),
    ],
)
def test_mcp_resource_template_uri_classification_and_discovery(
    res_name: "str",
    kwargs: "dict[str, Any]",
    expected_template: "str",
) -> "None":
    @mcp_resource(res_name, **kwargs)
    @get("/items/{item_id:str}", sync_to_thread=False)
    def templated_handler(item_id: "str") -> "str":
        return item_id

    metadata = get_mcp_metadata(templated_handler)
    assert metadata is not None
    assert metadata["resource_template"] == expected_template
    assert "resource_uri" not in metadata

    plugin = LitestarMCP()
    Litestar(route_handlers=[templated_handler], plugins=[plugin])
    assert res_name not in plugin.discovered_resources
    assert res_name in plugin.registry.templates
    assert plugin.registry.templates[res_name].template == expected_template


def test_mcp_resource_without_uri_omits_uri_keys() -> "None":
    @mcp_resource("plain_resource")
    @get("/plain", sync_to_thread=False)
    def plain_handler() -> "str":
        return "ok"

    metadata = get_mcp_metadata(plain_handler)
    assert metadata is not None
    assert "resource_uri" not in metadata
    assert "resource_template" not in metadata

    plugin = LitestarMCP()
    Litestar(route_handlers=[plain_handler], plugins=[plugin])
    assert "plain_resource" in plugin.discovered_resources
    assert "plain_resource" not in plugin.registry.templates


@pytest.mark.parametrize(
    ("uri", "uri_template", "expected_match"),
    [
        ("", None, "Invalid resource URI"),
        ("   ", None, "Invalid resource URI"),
        (None, "", "Invalid resource URI"),
        (None, "   ", "Invalid resource URI"),
        ("app://{0bad}", None, "Invalid variable in template"),
        ("app://{}", None, "Invalid variable in template"),
        ("app://{unclosed", None, "Unbalanced braces in template"),
        ("ui://a", "ui://{b}", "Cannot specify both"),
    ],
)
def test_mcp_resource_rejects_invalid_or_conflicting_uris(
    uri: "str | None",
    uri_template: "str | None",
    expected_match: "str",
) -> "None":
    with pytest.raises(ValueError, match=expected_match):
        mcp_resource("bad", uri=uri, uri_template=uri_template)


@pytest.mark.parametrize(
    ("res_name", "opt_kwargs"),
    [
        ("cfg_opt", {"mcp_resource_template": "ui://app/cfg.json"}),
        ("uri_opt", {"mcp_resource_uri": "ui://app/index.html"}),
    ],
)
def test_route_opt_brace_free_uri_registers_strictly_as_static_resource(
    res_name: "str",
    opt_kwargs: "dict[str, Any]",
) -> "None":
    @get("/cfg-opt", mcp_resource=res_name, sync_to_thread=False, **opt_kwargs)
    def cfg_opt_handler() -> "dict[str, bool]":
        return {"ok": True}

    plugin = LitestarMCP()
    Litestar(route_handlers=[cfg_opt_handler], plugins=[plugin])
    assert res_name in plugin.discovered_resources
    assert res_name not in plugin.registry.templates


@pytest.mark.parametrize(
    ("opt_kwargs", "expected_match"),
    [
        ({"mcp_resource_uri": "ui://a", "mcp_resource_template": "ui://{x}"}, "cannot specify both"),
        ({"mcp_resource_uri": ""}, "Invalid resource URI"),
        ({"mcp_resource_template": "app://{0bad}"}, "Invalid variable in template"),
    ],
)
def test_plugin_discovery_rejects_invalid_or_conflicting_resource_opts(
    opt_kwargs: "dict[str, Any]",
    expected_match: "str",
) -> "None":
    @get("/conflict", mcp_resource="conflict", sync_to_thread=False, **opt_kwargs)
    def conflict_handler() -> "str":
        return "x"

    with pytest.raises(ValueError, match=expected_match):
        Litestar(route_handlers=[conflict_handler], plugins=[LitestarMCP()])


def test_plugin_discovery_rejects_conflicting_metadata_uri_keys() -> "None":
    @get("/dual", sync_to_thread=False)
    def dual_handler() -> "str":
        return "x"

    set_mcp_metadata(
        dual_handler,
        {
            "type": "resource",
            "name": "dual",
            "resource_uri": "ui://a",
            "resource_template": "ui://{b}",
        },
    )
    with pytest.raises(ValueError, match="Cannot specify both"):
        Litestar(route_handlers=[dual_handler], plugins=[LitestarMCP()])


@pytest.mark.parametrize(
    ("output_schema", "expected"),
    [
        (
            {"type": "object", "properties": {"id": {"type": "string"}}},
            {"type": "object", "properties": {"id": {"type": "string"}}},
        ),
        (
            MappingProxyType({"type": "object", "properties": {"ok": {"type": "boolean"}}}),
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        (
            {"$ref": "#/$defs/Item", "$defs": {"Item": {"type": "object", "properties": {"x": {"type": "integer"}}}}},
            {"$ref": "#/$defs/Item", "$defs": {"Item": {"type": "object", "properties": {"x": {"type": "integer"}}}}},
        ),
        (True, True),
        (False, False),
        (None, None),
    ],
    ids=["object_dict", "mapping_proxy", "local_ref_object", "bool_true", "bool_false", "none_omitted"],
)
def test_mcp_tool_stores_output_schema_variants(output_schema: Any, expected: Any) -> None:
    @mcp_tool("typed_tool", output_schema=output_schema)
    @get("/typed", sync_to_thread=False)
    def typed_handler() -> dict[str, Any]:
        return {}

    metadata = get_mcp_metadata(typed_handler)
    assert metadata is not None
    if expected is None:
        assert "output_schema" not in metadata
    elif isinstance(expected, bool):
        assert metadata["output_schema"] is expected
    else:
        assert isinstance(metadata["output_schema"], dict)
        assert metadata["output_schema"] == expected


def test_mcp_tool_deep_copies_mutable_output_schema_mapping() -> None:
    source: dict[str, Any] = {"type": "object", "properties": {"name": {"type": "string"}}}

    @mcp_tool("copied_tool", output_schema=source)
    @get("/copied", sync_to_thread=False)
    def copied_handler() -> dict[str, Any]:
        return {"name": "a"}

    source["properties"]["name"]["type"] = "integer"
    metadata = get_mcp_metadata(copied_handler)
    assert metadata is not None
    assert metadata["output_schema"]["properties"]["name"]["type"] == "string"


@pytest.mark.parametrize(
    ("bad_schema", "exc_type", "expected_match"),
    [
        ("bad", TypeError, "output_schema must be a mapping, bool, or None"),
        (123, TypeError, "output_schema must be a mapping, bool, or None"),
        (["object"], TypeError, "output_schema must be a mapping, bool, or None"),
        ({"type": "string"}, ValueError, "output_schema mapping must resolve to root type 'object'"),
        ({"type": "array", "items": {"type": "string"}}, ValueError, "root type 'object'"),
        ({}, ValueError, "root type 'object'"),
        ({"$ref": "#/$defs/Missing", "$defs": {}}, ValueError, "root type 'object'"),
        (
            {"$ref": "#/$defs/A", "$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}}},
            ValueError,
            "root type 'object'",
        ),
    ],
    ids=["str", "int", "list", "string_schema", "array_schema", "empty_dict", "dangling_ref", "cyclic_ref"],
)
def test_mcp_tool_rejects_invalid_output_schema(
    bad_schema: Any,
    exc_type: type[Exception],
    expected_match: str,
) -> None:
    with pytest.raises(exc_type, match=expected_match):
        mcp_tool("invalid_tool", output_schema=bad_schema)


@pytest.mark.parametrize("stack_order", ["tool_outer", "resource_outer"])
def test_stacked_tool_resource_and_prompt_decorators_isolate_by_role(stack_order: str) -> None:
    if stack_order == "tool_outer":

        @mcp_tool("calc", title="Calc", meta={"ui": {"resourceUri": "ui://c"}})
        @mcp_resource("calc_ui", uri="ui://c", title="Calc UI", meta={"ui": {"prefersBorder": True}})
        @get("/calc", sync_to_thread=False)
        def dual_handler() -> str:
            return "<div/>"

        target: Any = dual_handler
    else:

        @mcp_resource("calc_ui", uri="ui://c", title="Calc UI", meta={"ui": {"prefersBorder": True}})
        @mcp_tool("calc", title="Calc", meta={"ui": {"resourceUri": "ui://c"}})
        @get("/calc", sync_to_thread=False)
        def reverse_handler() -> str:
            return "<div/>"

        target = reverse_handler

    tool_meta = get_mcp_metadata(target, kind="tool")
    resource_meta = get_mcp_metadata(target, kind="resource")
    assert tool_meta is not None
    assert resource_meta is not None
    assert tool_meta["name"] == "calc"
    assert tool_meta["title"] == "Calc"
    assert tool_meta["meta"] == {"ui": {"resourceUri": "ui://c"}}
    assert resource_meta["name"] == "calc_ui"
    assert resource_meta["title"] == "Calc UI"
    assert resource_meta["meta"] == {"ui": {"prefersBorder": True}}
    assert get_mcp_metadata(target, kind="prompt") is None
    all_entries = get_all_mcp_metadata(target)
    assert len(all_entries) == 2
    assert {entry["type"] for entry in all_entries} == {"tool", "resource"}

    @mcp_prompt("triple_prompt", title="Prompt Title", meta={"surface": "chat"})
    @mcp_resource("triple_res", uri="ui://triple", title="Res Title")
    @mcp_tool("triple_tool", title="Tool Title")
    def triple_fn() -> str:
        return "triple"

    assert len(get_all_mcp_metadata(triple_fn)) == 3
    assert (get_mcp_metadata(triple_fn, kind="tool") or {})["title"] == "Tool Title"
    assert (get_mcp_metadata(triple_fn, kind="resource") or {})["title"] == "Res Title"
    assert (get_mcp_metadata(triple_fn, kind="prompt") or {})["title"] == "Prompt Title"


def test_role_scoped_render_description_and_external_tool_meta_isolation() -> None:
    @mcp_tool("dual_desc_t", description="Tool prose", when_to_use="Call tool")
    @mcp_resource("dual_desc_r", description="Resource prose", when_to_use="Read resource")
    @get("/dual-desc", sync_to_thread=False)
    def dual_desc() -> str:
        return "ok"

    tool_rendered = render_description(dual_desc, dual_desc.fn, kind="tool", fallback_name="dual_desc_t")
    res_rendered = render_description(dual_desc, dual_desc.fn, kind="resource", fallback_name="dual_desc_r")
    assert tool_rendered == "Tool prose\n\n## When to use\nCall tool"
    assert res_rendered == "Resource prose\n\n## When to use\nRead resource"

    def external_callable() -> str:
        return "ext"

    setattr(external_callable, "_mcp_tool_meta", {"name": "ext_tool", "description": "External tool"})
    assert get_mcp_metadata(external_callable, kind="resource") is None
    assert get_mcp_metadata(external_callable, kind="prompt") is None
    ext_tool_meta = get_mcp_metadata(external_callable, kind="tool")
    assert ext_tool_meta is not None
    assert ext_tool_meta["type"] == "tool"
    assert ext_tool_meta["name"] == "ext_tool"
    assert len(get_all_mcp_metadata(external_callable)) == 1


@pytest.mark.parametrize("decorator_kind", ["tool", "resource", "prompt"])
@pytest.mark.parametrize(
    ("title", "icons", "meta", "expect_present"),
    [
        (
            "  Display Title  ",
            [{"src": "https://example.com/icon.svg", "mimeType": "image/svg+xml"}],
            {"ui": {"prefersBorder": True}},
            True,
        ),
        (None, None, None, False),
        ("", [], {}, False),
        ("   ", [], {}, False),
    ],
    ids=["populated", "all_none", "all_empty", "whitespace_title"],
)
def test_decorators_normalize_and_omit_empty_descriptor_fields(
    decorator_kind: str,
    title: str | None,
    icons: list[dict[str, Any]] | None,
    meta: dict[str, Any] | None,
    expect_present: bool,
) -> None:
    def sample() -> str:
        return "v"

    icons_copy = [{"src": item["src"], "mimeType": item["mimeType"]} for item in icons] if icons is not None else None
    meta_copy = (
        {"ui": dict(meta["ui"])}
        if isinstance(meta, dict) and isinstance(meta.get("ui"), dict)
        else (dict(meta) if meta is not None else None)
    )

    if decorator_kind == "tool":
        decorated: Any = mcp_tool("d_tool", title=title, icons=icons_copy, meta=meta_copy)(sample)
    elif decorator_kind == "resource":
        decorated = mcp_resource("d_res", title=title, icons=icons_copy, meta=meta_copy)(sample)
    else:
        decorated = mcp_prompt("d_prompt", title=title, icons=icons_copy, meta=meta_copy)(sample)

    stored = get_mcp_metadata(decorated, kind=decorator_kind)
    assert stored is not None
    if expect_present:
        assert stored["title"] == "Display Title"
        assert stored["icons"] == [{"src": "https://example.com/icon.svg", "mimeType": "image/svg+xml"}]
        assert stored["meta"] == {"ui": {"prefersBorder": True}}
        assert icons_copy is not None and meta_copy is not None
        icons_copy[0]["src"] = "mutated"
        meta_copy["ui"]["prefersBorder"] = False
        refetched = get_mcp_metadata(decorated, kind=decorator_kind)
        assert refetched is not None
        assert refetched["icons"][0]["src"] == "https://example.com/icon.svg"
        assert refetched["meta"]["ui"]["prefersBorder"] is True
    else:
        assert "title" not in stored
        assert "icons" not in stored
        assert "meta" not in stored
