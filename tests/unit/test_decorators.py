"""Tests for MCP decorators."""

from litestar import Litestar
from litestar.handlers import get

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.core.tools import tool
from litestar_mcp.utils import get_mcp_metadata, mcp_prompt, mcp_resource, mcp_tool


def test_mcp_tool_decorator_storage() -> "None":
    @mcp_tool(name="test_tool")
    @get("/", sync_to_thread=False)
    def my_handler() -> "str":
        return "hello"

    metadata = get_mcp_metadata(my_handler)
    assert metadata is not None
    assert metadata["type"] == "tool"
    assert metadata["name"] == "test_tool"
    # Ensure no attribute mutation on the function itself if possible,
    # or at least that it's correctly retrieved.
    # Note: Litestar handlers might be tricky, but we want a central way to get this.


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
