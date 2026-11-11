"""End-to-end tests for :class:`MCPOptKeys` — renamed opt keys work across
discovery (``plugin.py``) and description rendering (routes).
"""

from typing import Any, Literal, get_type_hints

import pytest
from litestar import Litestar, get, post
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.mcp.config import UI_EXTENSION, UI_MIME_TYPE, MCPConfig, MCPOptKeys
from tests.unit.conftest import mcp_post


def _rpc(client: "TestClient[Any]", method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
    data: dict[str, Any] = mcp_post(client, method, params).json()
    return data


def test_renamed_tool_and_resource_opt_keys_drive_discovery() -> "None":
    opt_keys = MCPOptKeys(tool="x_tool", resource="x_resource")

    @get("/users", opt={"x_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> "list[dict[str, Any]]":
        """List users."""
        return []

    @get("/config", opt={"x_resource": "app_config"}, sync_to_thread=False)
    def get_config() -> "dict[str, Any]":
        """App config."""
        return {}

    # Handlers using the *default* opt keys must NOT be discovered under the renamed config.
    @get("/ignored-tool", opt={"mcp_tool": "nope_tool"}, sync_to_thread=False)
    def ignored_tool() -> "dict[str, Any]":
        """Should not be registered."""
        return {}

    plugin = LitestarMCP(MCPConfig(opt_keys=opt_keys))
    app = Litestar(route_handlers=[list_users, get_config, ignored_tool], plugins=[plugin])

    assert "list_users" in plugin.discovered_tools
    assert "app_config" in plugin.discovered_resources
    assert "nope_tool" not in plugin.discovered_tools

    with TestClient(app=app) as client:
        tools = _rpc(client, "tools/list")["result"]["tools"]
        resources = _rpc(client, "resources/list")["result"]["resources"]

        tool_names = {t["name"] for t in tools}
        assert "list_users" in tool_names
        assert "nope_tool" not in tool_names
        resource_names = {r["name"] for r in resources}
        assert "app_config" in resource_names


def test_renamed_description_opt_keys_render_through_endpoints() -> "None":
    opt_keys = MCPOptKeys(
        tool="x_tool",
        description="x_description",
        when_to_use="x_when_to_use",
    )

    @get(
        "/users",
        opt={
            "x_tool": "list_users",
            "x_description": "LLM prose for users.",
            "x_when_to_use": "Asked for users.",
        },
        sync_to_thread=False,
    )
    def list_users() -> "list[dict[str, Any]]":
        """ignored-docstring."""
        return []

    app = Litestar(
        route_handlers=[list_users],
        plugins=[LitestarMCP(MCPConfig(opt_keys=opt_keys))],
    )

    with TestClient(app=app) as client:
        tools = _rpc(client, "tools/list")["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "list_users")
        assert descr.startswith("LLM prose for users.")
        assert "## When to use\nAsked for users." in descr
        assert "ignored-docstring" not in descr


def test_default_opt_keys_unchanged_when_config_omits_opt_keys() -> "None":
    """Regression guard: apps that don't set opt_keys still use the ``mcp_*`` defaults."""

    @get("/users", opt={"mcp_tool": "list_users", "mcp_description": "prose"}, sync_to_thread=False)
    def list_users() -> "list[dict[str, Any]]":
        """Docstring."""
        return []

    plugin = LitestarMCP(MCPConfig())  # No opt_keys override.
    app = Litestar(route_handlers=[list_users], plugins=[plugin])
    assert plugin.config.opt_keys == MCPOptKeys()

    with TestClient(app=app) as client:
        tools = _rpc(client, "tools/list")["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "list_users")
        assert descr == "prose"


def test_modern_tool_policy_opt_keys_can_be_renamed() -> "None":
    opt_keys = MCPOptKeys(
        required_client_capabilities="x_required_capabilities",
        task_input_before_start="x_task_input_before_start",
        annotations="x_annotations",
        read_only="x_read_only",
    )

    assert opt_keys.required_client_capabilities == "x_required_capabilities"
    assert opt_keys.task_input_before_start == "x_task_input_before_start"
    assert opt_keys.annotations == "x_annotations"
    assert opt_keys.read_only == "x_read_only"


def test_renamed_annotations_and_read_only_opt_keys_render_through_endpoints() -> "None":
    opt_keys = MCPOptKeys(
        tool="x_tool",
        annotations="x_annotations",
        read_only="x_read_only",
    )

    @post(
        "/search",
        opt={
            "x_tool": "search_items",
            "x_read_only": True,
            "x_annotations": {"idempotentHint": True, "title": "Search Items"},
        },
        sync_to_thread=False,
    )
    def search_items() -> "list[dict[str, Any]]":
        """Search items."""
        return []

    app = Litestar(
        route_handlers=[search_items],
        plugins=[LitestarMCP(MCPConfig(opt_keys=opt_keys))],
    )

    with TestClient(app=app) as client:
        tools = _rpc(client, "tools/list")["result"]["tools"]
        tool_entry = next(t for t in tools if t["name"] == "search_items")
        assert tool_entry["annotations"] == {
            "readOnlyHint": True,
            "idempotentHint": True,
            "title": "Search Items",
        }


def test_output_schema_opt_key_and_auto_output_schema_config_defaults() -> None:
    default_opt_keys = MCPOptKeys()
    renamed_opt_keys = MCPOptKeys(output_schema="x_output_schema")
    default_config = MCPConfig()
    enabled_config = MCPConfig(auto_output_schema=True)

    assert default_opt_keys.output_schema == "mcp_output_schema"
    assert renamed_opt_keys.output_schema == "x_output_schema"
    assert default_config.auto_output_schema is False
    assert enabled_config.auto_output_schema is True

    opt_hints = get_type_hints(MCPOptKeys)
    cfg_hints = get_type_hints(MCPConfig)
    assert opt_hints["output_schema"] is str
    assert cfg_hints["auto_output_schema"] is bool


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt"])
def test_output_schema_opt_key_resolves_across_all_kinds(kind: Literal["tool", "resource", "prompt"]) -> None:
    renamed = MCPOptKeys(output_schema="x_output_schema")
    assert MCPOptKeys().for_field("output_schema", kind) == "mcp_output_schema"
    assert renamed.for_field("output_schema", kind) == "x_output_schema"


def test_ui_constants_and_descriptor_opt_key_defaults() -> None:
    defaults = MCPOptKeys()
    cfg = MCPConfig()
    opt_hints = get_type_hints(MCPOptKeys)
    cfg_hints = get_type_hints(MCPConfig)

    assert UI_EXTENSION == "io.modelcontextprotocol/ui"
    assert UI_MIME_TYPE == "text/html;profile=mcp-app"
    assert defaults.tool_title == "mcp_tool_title"
    assert defaults.tool_icons == "mcp_tool_icons"
    assert defaults.tool_meta == "mcp_tool_meta"
    assert defaults.resource_title == "mcp_resource_title"
    assert defaults.resource_icons == "mcp_resource_icons"
    assert defaults.resource_meta == "mcp_resource_meta"
    assert defaults.prompt_meta == "mcp_prompt_meta"
    assert cfg.ui_mime_types is None

    assert opt_hints["tool_title"] is str
    assert opt_hints["tool_icons"] is str
    assert opt_hints["tool_meta"] is str
    assert opt_hints["resource_title"] is str
    assert opt_hints["resource_icons"] is str
    assert opt_hints["resource_meta"] is str
    assert opt_hints["prompt_meta"] is str
    assert "Sequence" in str(cfg_hints["ui_mime_types"])
    assert "None" in str(cfg_hints["ui_mime_types"])


@pytest.mark.parametrize(
    ("field_name", "kind", "default_key", "custom_key"),
    [
        ("description", "tool", "mcp_description", "x_t_desc"),
        ("description", "resource", "mcp_resource_description", "x_r_desc"),
        ("description", "prompt", "mcp_prompt_description", "x_p_desc"),
        ("title", "tool", "mcp_tool_title", "x_t_title"),
        ("title", "resource", "mcp_resource_title", "x_r_title"),
        ("title", "prompt", "mcp_prompt_title", "x_p_title"),
        ("icons", "tool", "mcp_tool_icons", "x_t_icons"),
        ("icons", "resource", "mcp_resource_icons", "x_r_icons"),
        ("icons", "prompt", "mcp_prompt_icons", "x_p_icons"),
        ("meta", "tool", "mcp_tool_meta", "x_t_meta"),
        ("meta", "resource", "mcp_resource_meta", "x_r_meta"),
        ("meta", "prompt", "mcp_prompt_meta", "x_p_meta"),
    ],
)
def test_for_field_resolves_role_specific_descriptor_keys(
    field_name: str,
    kind: Literal["tool", "resource", "prompt"],
    default_key: str,
    custom_key: str,
) -> None:
    renamed = MCPOptKeys(
        description="x_t_desc",
        resource_description="x_r_desc",
        prompt_description="x_p_desc",
        tool_title="x_t_title",
        resource_title="x_r_title",
        prompt_title="x_p_title",
        tool_icons="x_t_icons",
        resource_icons="x_r_icons",
        prompt_icons="x_p_icons",
        tool_meta="x_t_meta",
        resource_meta="x_r_meta",
        prompt_meta="x_p_meta",
    )
    assert MCPOptKeys().for_field(field_name, kind) == default_key
    assert renamed.for_field(field_name, kind) == custom_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ((), ()),
        ([], ()),
        (
            [" text/html;profile=mcp-app ", "text/html;profile=mcp-app", "text/html"],
            ("text/html;profile=mcp-app", "text/html"),
        ),
    ],
    ids=["none", "empty_tuple", "empty_list", "deduped_and_stripped"],
)
def test_mcp_config_normalizes_valid_ui_mime_types(
    raw: list[str] | tuple[str, ...] | None,
    expected: tuple[str, ...] | None,
) -> None:
    assert MCPConfig(ui_mime_types=raw).ui_mime_types == expected


@pytest.mark.parametrize(
    "bad_value",
    ["text/html;profile=mcp-app", b"text/html", 123, [""], ["   "], [123]],
    ids=["bare_str", "bytes", "int", "empty_item", "whitespace_item", "non_str_item"],
)
def test_mcp_config_rejects_invalid_ui_mime_types(bad_value: Any) -> None:
    with pytest.raises(ValueError, match="ui_mime_types"):
        MCPConfig(ui_mime_types=bad_value)


def test_renamed_descriptor_opt_keys_render_end_to_end() -> None:
    opt_keys = MCPOptKeys(
        tool="x_t",
        tool_title="x_tt",
        tool_icons="x_ti",
        tool_meta="x_tm",
        resource="x_r",
        resource_uri="x_ru",
        resource_mime_type="x_rmime",
        resource_title="x_rt",
        resource_icons="x_ri",
        resource_meta="x_rm",
        prompt="x_p",
        prompt_title="x_pt",
        prompt_icons="x_pi",
        prompt_meta="x_pm",
    )
    icon_list = [{"src": "https://example.com/icon.svg", "mimeType": "image/svg+xml"}]

    @get(
        "/panel-tool",
        opt={
            "x_t": "open_panel",
            "x_tt": "Open Panel",
            "x_ti": icon_list,
            "x_tm": {"ui": {"resourceUri": "ui://custom/panel.html"}},
        },
        sync_to_thread=False,
    )
    def open_panel() -> dict[str, str]:
        return {"status": "ready"}

    @get(
        "/panel-ui",
        opt={
            "x_r": "panel_ui",
            "x_ru": "ui://custom/panel.html",
            "x_rmime": UI_MIME_TYPE,
            "x_rt": "Panel View",
            "x_ri": icon_list,
            "x_rm": {"ui": {"prefersBorder": True}},
        },
        sync_to_thread=False,
    )
    def panel_ui() -> str:
        return "<main>Panel</main>"

    @get(
        "/panel-prompt",
        opt={
            "x_p": "guide_panel",
            "x_pt": "Guide Panel",
            "x_pi": icon_list,
            "x_pm": {"tier": "pro"},
        },
        sync_to_thread=False,
    )
    def guide_panel() -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": {"type": "text", "text": "Help"}}]}

    app = Litestar(
        route_handlers=[open_panel, panel_ui, guide_panel],
        plugins=[LitestarMCP(MCPConfig(opt_keys=opt_keys))],
    )
    with TestClient(app=app) as client:
        discover = _rpc(client, "server/discover")["result"]
        tools = _rpc(client, "tools/list")["result"]["tools"]
        resources = _rpc(client, "resources/list")["result"]["resources"]
        read_res = _rpc(client, "resources/read", {"uri": "ui://custom/panel.html"})["result"]
        prompts = _rpc(client, "prompts/list")["result"]["prompts"]

    assert discover["capabilities"]["extensions"][UI_EXTENSION] == {"mimeTypes": [UI_MIME_TYPE]}
    tool_entry = next(item for item in tools if item["name"] == "open_panel")
    assert tool_entry["title"] == "Open Panel"
    assert tool_entry["icons"] == icon_list
    assert tool_entry["_meta"] == {"ui": {"resourceUri": "ui://custom/panel.html"}}

    res_entry = next(item for item in resources if item["name"] == "panel_ui")
    assert res_entry["title"] == "Panel View"
    assert res_entry["icons"] == icon_list
    assert res_entry["_meta"] == {"ui": {"prefersBorder": True}}
    assert read_res["contents"] == [
        {
            "uri": "ui://custom/panel.html",
            "mimeType": UI_MIME_TYPE,
            "text": "<main>Panel</main>",
            "_meta": {"ui": {"prefersBorder": True}},
        }
    ]

    prompt_entry = next(item for item in prompts if item["name"] == "guide_panel")
    assert prompt_entry["title"] == "Guide Panel"
    assert prompt_entry["icons"] == icon_list
    assert prompt_entry["_meta"] == {"tier": "pro"}
