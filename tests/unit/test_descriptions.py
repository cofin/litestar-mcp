"""Unit tests for the ``render_description`` helper.

Covers the precedence matrix (opt > decorator > docstring > fallback) and
structured-field rendering (``## When to use`` / ``## Returns`` /
``## Instructions`` sections).
"""

from litestar.handlers import get

from litestar_mcp._descriptions import extract_description_sources, render_description
from litestar_mcp.decorators import mcp_resource, mcp_tool
from litestar_mcp.utils import get_handler_function


class TestToolDescriptionPrecedence:
    def test_opt_wins_over_decorator_and_docstring(self) -> None:
        @mcp_tool("foo", description="from-decorator")
        @get("/", opt={"mcp_description": "from-opt"})
        def handler() -> str:
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-opt"

    def test_decorator_wins_over_docstring(self) -> None:
        @mcp_tool("foo", description="from-decorator")
        @get("/")
        def handler() -> str:
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-decorator"

    def test_docstring_wins_over_fallback(self) -> None:
        @mcp_tool("foo")
        @get("/")
        def handler() -> str:
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-docstring."

    def test_fallback_when_nothing_set(self) -> None:
        @mcp_tool("foo")
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "Tool: foo"

    def test_empty_string_treated_as_absent(self) -> None:
        @mcp_tool("foo", description="")
        @get("/", opt={"mcp_description": ""})
        def handler() -> str:
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-docstring."


class TestStructuredRendering:
    def test_plain_when_no_structured_fields(self) -> None:
        @mcp_tool("foo", description="simple")
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert result == "simple"
        assert "##" not in result

    def test_sections_when_structured_fields_set(self) -> None:
        @mcp_tool(
            "foo",
            description="Do the thing.",
            when_to_use="When the user asks for a thing.",
            returns="A Thing struct.",
            agent_instructions="Never do this without confirming.",
        )
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert result.startswith("Do the thing.")
        assert "## When to use\nWhen the user asks for a thing." in result
        assert "## Returns\nA Thing struct." in result
        assert "## Instructions\nNever do this without confirming." in result
        assert result.index("## When to use") < result.index("## Returns") < result.index("## Instructions")

    def test_partial_structured_fields(self) -> None:
        @mcp_tool("foo", description="Do.", when_to_use="Sometimes.")
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert "## When to use\nSometimes." in result
        assert "## Returns" not in result
        assert "## Instructions" not in result

    def test_structured_false_ignores_structured_fields(self) -> None:
        @mcp_tool("foo", description="Do.", when_to_use="Sometimes.")
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo", structured=False)
        assert result == "Do."
        assert "##" not in result

    def test_opt_form_structured_fields(self) -> None:
        @get(
            "/",
            opt={
                "mcp_description": "opt-prose",
                "mcp_when_to_use": "opt-wtu",
                "mcp_returns": "opt-ret",
                "mcp_agent_instructions": "opt-ai",
            },
        )
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert result.startswith("opt-prose")
        assert "## When to use\nopt-wtu" in result
        assert "## Returns\nopt-ret" in result
        assert "## Instructions\nopt-ai" in result


class TestResourceDescriptionPrecedence:
    def test_opt_key_is_mcp_resource_description(self) -> None:
        @mcp_resource("bar", description="decorator-desc")
        @get("/", opt={"mcp_resource_description": "opt-desc"})
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "opt-desc"

    def test_resource_docstring_wins_over_fallback(self) -> None:
        @mcp_resource("bar")
        @get("/")
        def handler() -> str:
            """res-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "res-docstring."

    def test_resource_fallback(self) -> None:
        @mcp_resource("bar")
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "Resource: bar"

    def test_resource_opt_description_key_does_not_apply_to_resources(self) -> None:
        """``mcp_description`` is the tool key; resources use ``mcp_resource_description``."""

        @mcp_resource("bar")
        @get("/", opt={"mcp_description": "wrong-key"})
        def handler() -> str:
            """res-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "res-docstring."


class TestCustomOptKeys:
    """Downstream apps can rename opt keys via ``MCPConfig.opt_keys``."""

    def test_renamed_tool_description_opt_key_is_honoured(self) -> None:
        from litestar_mcp.config import MCPOptKeys

        opt_keys = MCPOptKeys(description="x_mcp_description")

        @mcp_tool("foo")
        @get("/", opt={"x_mcp_description": "opt-prose"})
        def handler() -> str:
            """Docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo", opt_keys=opt_keys) == "opt-prose"
        # Default opt key is ignored when renamed
        assert opt_keys.description != "mcp_description"
        default_result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert default_result == "Docstring."

    def test_renamed_resource_description_opt_key(self) -> None:
        from litestar_mcp.config import MCPOptKeys

        opt_keys = MCPOptKeys(resource_description="x_mcp_resource_description")

        @mcp_resource("bar")
        @get("/", opt={"x_mcp_resource_description": "opt-res"})
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar", opt_keys=opt_keys) == "opt-res"

    def test_renamed_structured_field_opt_keys(self) -> None:
        from litestar_mcp.config import MCPOptKeys

        opt_keys = MCPOptKeys(
            description="x_desc",
            when_to_use="x_wtu",
            returns="x_ret",
            agent_instructions="x_ai",
        )

        @get(
            "/",
            opt={
                "x_desc": "d",
                "x_wtu": "wtu",
                "x_ret": "ret",
                "x_ai": "ai",
            },
        )
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo", opt_keys=opt_keys)
        assert result.startswith("d")
        assert "## When to use\nwtu" in result
        assert "## Returns\nret" in result
        assert "## Instructions\nai" in result


class TestExtractDescriptionSources:
    def test_returns_structured_dataclass(self) -> None:
        @mcp_tool("foo", description="d", when_to_use="wtu", returns="r", agent_instructions="ai")
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        sources = extract_description_sources(handler, fn, kind="tool", fallback_name="foo")
        assert sources.description == "d"
        assert sources.when_to_use == "wtu"
        assert sources.returns == "r"
        assert sources.agent_instructions == "ai"

    def test_missing_structured_fields_are_none(self) -> None:
        @mcp_tool("foo", description="d")
        @get("/")
        def handler() -> str:
            return ""

        fn = get_handler_function(handler)
        sources = extract_description_sources(handler, fn, kind="tool", fallback_name="foo")
        assert sources.description == "d"
        assert sources.when_to_use is None
        assert sources.returns is None
        assert sources.agent_instructions is None
