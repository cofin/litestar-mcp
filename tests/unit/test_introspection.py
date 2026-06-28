"""Tests for signature extraction, parameter mapping, and description rendering."""

from typing import Annotated, Any

import pytest
from litestar import Litestar, Request, get
from litestar.di import Provide
from litestar.params import Parameter
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, mcp_resource, mcp_tool
from litestar_mcp.utils import (
    extract_description_sources,
    get_handler_function,
    render_description,
)
from litestar_mcp.utils.handler_signature import (
    extract_advertised_handler_arguments,
    parameter_aliases,
)
from tests.unit.conftest import create_app_with_handler

pytestmark = pytest.mark.unit


# ==============================================================================
# 1. Parameter Aliases Coverage (from test_parameter_aliases.py)
# ==============================================================================


class TestParameterAliases:
    def test_no_aliases_returns_empty_map(self) -> None:
        def handler(name: str, age: int) -> dict[str, Any]:
            return {"name": name, "age": age}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_query_alias_is_included(self) -> None:
        def handler(
            is_paid: Annotated[bool, Parameter(query="isPaid")] = False,
        ) -> dict[str, Any]:
            return {"is_paid": is_paid}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"isPaid": "is_paid"}

    def test_query_matching_python_name_is_omitted(self) -> None:
        def handler(
            page: Annotated[int, Parameter(query="page")] = 1,
        ) -> dict[str, Any]:
            return {"page": page}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_header_alias_ignored(self) -> None:
        def handler(
            tenant_id: Annotated[str, Parameter(header="X-Tenant")] = "",
        ) -> dict[str, Any]:
            return {"tenant_id": tenant_id}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_no_query_omitted_even_when_annotated(self) -> None:
        def handler(
            n: Annotated[int, Parameter(description="count")] = 0,
        ) -> dict[str, Any]:
            return {"n": n}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}


# ==============================================================================
# 2. Handler Signature Extraction Coverage (from test_handler_signature.py)
# ==============================================================================


class TestHandlerSignature:
    def test_extract_advertised_handler_arguments_filters_di_reserved_and_path_params(self) -> None:
        async def provide_secret() -> str:
            return "secret"

        def handler(
            item_id: str,
            request: Request[Any, Any, Any],
            secret: str,
            q: str,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {"item_id": item_id, "q": q, "limit": limit, "secret": secret, "path": request.url.path}

        _, route_handler = create_app_with_handler(
            handler,
            route_path="/items/{item_id:str}",
            mcp_prompt="item_prompt",
            dependencies={"secret": Provide(provide_secret)},
        )

        args = extract_advertised_handler_arguments(route_handler, path_parameters={"item_id"})
        assert [(arg["name"], arg["required"]) for arg in args] == [("q", True), ("limit", False)]

    def test_extract_advertised_handler_arguments_uses_query_alias_and_docstring_description(self) -> None:
        def handler(
            is_paid: Annotated[bool, Parameter(query="isPaid")],
            page_size: Annotated[int, Parameter(query="pageSize")] = 50,
        ) -> dict[str, Any]:
            """List invoices.

            Args:
                is_paid: Whether the invoice is paid.
                page_size: Number of invoices to return.
            """
            return {"is_paid": is_paid, "page_size": page_size}

        _, route_handler = create_app_with_handler(handler)
        args = extract_advertised_handler_arguments(route_handler)
        assert args == [
            {"name": "isPaid", "description": "Whether the invoice is paid.", "required": True},
            {"name": "pageSize", "description": "Number of invoices to return.", "required": False},
        ]

    def test_extract_advertised_handler_arguments_filters_litestar_signature_namespace(self) -> None:
        def handler(headers: dict[str, str], text: str) -> dict[str, str]:
            return {"text": text, "headers": str(headers)}

        _, route_handler = create_app_with_handler(handler)
        args = extract_advertised_handler_arguments(route_handler)
        assert [arg["name"] for arg in args] == ["text"]

    def test_extract_advertised_handler_arguments_filters_dishka_resolved_provider_params(self) -> None:
        from dishka import Provider, Scope, make_async_container, provide
        from dishka.integrations.litestar import LitestarProvider, setup_dishka
        from litestar import Litestar, get
        from litestar.params import FromQuery

        from tests.unit.conftest import get_handler_from_app

        class Driver:
            pass

        class TaskService:
            pass

        class DishkaProvider(Provider):
            scope = Scope.REQUEST

            @provide
            def driver(self) -> Driver:
                return Driver()

        async def provide_task_service(driver: Driver) -> TaskService:
            return TaskService()

        @get(
            "/hello",
            opt={"mcp_tool": "hello"},
            dependencies={"task_service": Provide(provide_task_service)},
            sync_to_thread=False,
        )
        def hello(name: FromQuery[str]) -> dict[str, str]:
            return {"hello": name}

        app = Litestar(route_handlers=[hello])
        container = make_async_container(LitestarProvider(), DishkaProvider())
        setup_dishka(container=container, app=app)
        route_handler = get_handler_from_app(app, "/hello")

        args = extract_advertised_handler_arguments(route_handler)
        assert [arg["name"] for arg in args] == ["name"]

    def test_import_iter_dependency_input_parameters(self) -> None:
        from litestar_mcp.utils.handler_signature import iter_dependency_input_parameters

        # A simple test to verify it works
        def handler(q: str) -> None:
            pass

        _, route_handler = create_app_with_handler(handler)
        params = iter_dependency_input_parameters(route_handler)
        assert isinstance(params, list)

    def test_import_parameter_aliases(self) -> None:
        from litestar_mcp.utils.handler_signature import parameter_aliases

        def handler(q: Annotated[str, Parameter(query="query_alias")]) -> None:
            pass

        _, route_handler = create_app_with_handler(handler)
        aliases = parameter_aliases(route_handler)
        assert aliases == {"query_alias": "q"}


# ==============================================================================
# 3. Description Precedence & Structured Rendering (from test_descriptions.py)
# ==============================================================================


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


# ==============================================================================
# 4. Endpoint Description Rendering Tests (from test_description_rendering_endpoints.py)
# ==============================================================================


class TestDescriptionRenderingEndpoints:
    @staticmethod
    def _make_app(*handlers: Any) -> Litestar:
        return Litestar(route_handlers=list(handlers), plugins=[LitestarMCP(MCPConfig())])

    @staticmethod
    def _init_and_get_session(client: TestClient[Any]) -> str:
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "it"}},
            },
        )
        assert init.status_code == 200, init.text
        sid = init.headers["mcp-session-id"]
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": sid},
        )
        return sid

    @staticmethod
    def _rpc(client: TestClient[Any], method: str, sid: str, params: "dict[str, Any] | None" = None) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        resp = client.post("/mcp", json=body, headers={"Mcp-Session-Id": sid})
        return resp.json()  # type: ignore[no-any-return]

    def test_tools_list_returns_decorator_description(self) -> None:
        @mcp_tool("t", description="LLM prose", when_to_use="When asked")
        @get("/x", sync_to_thread=False)
        def handler() -> dict[str, Any]:
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            sid = self._init_and_get_session(client)
            result = self._rpc(client, "tools/list", sid)
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr.startswith("LLM prose")
            assert "## When to use\nWhen asked" in descr

    def test_tools_list_opt_form_overrides_docstring(self) -> None:
        @get("/x", opt={"mcp_tool": "t", "mcp_description": "opt-prose"}, sync_to_thread=False)
        def handler() -> dict[str, Any]:
            """docstring-prose."""
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            sid = self._init_and_get_session(client)
            result = self._rpc(client, "tools/list", sid)
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr == "opt-prose"

    def test_tools_list_docstring_fallback_unchanged(self) -> None:
        """v0.4.0 regression guard: plain docstring → unchanged plain string."""

        @get("/x", opt={"mcp_tool": "t"}, sync_to_thread=False)
        def handler() -> dict[str, Any]:
            """plain-docstring."""
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            sid = self._init_and_get_session(client)
            result = self._rpc(client, "tools/list", sid)
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr == "plain-docstring."
            assert "##" not in descr

    def test_resources_list_returns_rendered_description(self) -> None:
        @mcp_resource("r", description="res-prose", when_to_use="Sometimes")
        @get("/y", sync_to_thread=False)
        def handler() -> dict[str, Any]:
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            sid = self._init_and_get_session(client)
            result = self._rpc(client, "resources/list", sid)
            resources = result["result"]["resources"]
            descr = next(r["description"] for r in resources if r["name"] == "r")
            assert descr.startswith("res-prose")
            assert "## When to use\nSometimes" in descr

    def test_resources_list_opt_form_resource_description_key(self) -> None:
        @get(
            "/y",
            opt={"mcp_resource": "r", "mcp_resource_description": "opt-res-prose"},
            sync_to_thread=False,
        )
        def handler() -> dict[str, Any]:
            """docstring-prose."""
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            sid = self._init_and_get_session(client)
            result = self._rpc(client, "resources/list", sid)
            resources = result["result"]["resources"]
            descr = next(r["description"] for r in resources if r["name"] == "r")
            assert descr == "opt-res-prose"

    def test_agent_card_and_mcp_server_manifest_match_tools_list(self) -> None:
        @mcp_tool("t", description="primary", when_to_use="wtu", returns="r")
        @get("/x", sync_to_thread=False)
        def handler() -> dict[str, Any]:
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            sid = self._init_and_get_session(client)
            tl = self._rpc(client, "tools/list", sid)
            tl_descr = next(t["description"] for t in tl["result"]["tools"] if t["name"] == "t")

            agent_card = client.get("/.well-known/agent-card.json").json()
            ac_descr = next(s["description"] for s in agent_card["skills"] if s["id"] == "t")

            mcp_manifest = client.get("/.well-known/mcp-server.json").json()
            mm_descr = next(t["description"] for t in mcp_manifest["tools"] if t["name"] == "t")

            assert tl_descr == ac_descr == mm_descr
            assert "## When to use\nwtu" in tl_descr
            assert "## Returns\nr" in tl_descr

    @pytest.mark.parametrize(
        "doc",
        [
            "  leading-spaces-and-trailing-spaces.  ",
            "\nleading newline.",
            "trailing newline.\n",
        ],
    )
    def test_tools_list_docstring_stripped_like_legacy(self, doc: str) -> None:
        """Plain-mode output must equal legacy ``(fn.__doc__ or ...).strip()``."""

        @get("/x", opt={"mcp_tool": "t"}, sync_to_thread=False)
        def handler() -> dict[str, Any]:
            return {}

        handler.fn.__doc__ = doc

        with TestClient(app=self._make_app(handler)) as client:
            sid = self._init_and_get_session(client)
            result = self._rpc(client, "tools/list", sid)
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr == doc.strip()
