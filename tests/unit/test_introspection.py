"""Tests for signature extraction, parameter mapping, and description rendering."""

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import msgspec
import pytest
from litestar import Litestar, Request, get, post
from litestar.di import Provide
from litestar.params import CookieParameter, HeaderParameter, Parameter, ParameterKwarg, QueryParameter
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, mcp_resource, mcp_tool, validate_tool_descriptions
from litestar_mcp.core.schema_builder import generate_output_schema_for_handler, generate_schema_for_handler
from litestar_mcp.utils import (
    extract_description_sources,
    get_handler_function,
    render_description,
    strip_docstring_sections,
)
from litestar_mcp.utils.handler_signature import (
    extract_advertised_handler_arguments,
    parameter_aliases,
)
from tests.unit.conftest import create_app_with_handler, mcp_post

pytestmark = pytest.mark.unit


class _IntrospectionDriver:
    pass


class _IntrospectionTaskService:
    pass


# ==============================================================================
# 1. Parameter Aliases Coverage (from test_parameter_aliases.py)
# ==============================================================================


class TestParameterAliases:
    def test_no_aliases_returns_empty_map(self) -> "None":
        def handler(name: "str", age: "int") -> "dict[str, Any]":
            return {"name": name, "age": age}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_query_alias_is_included(self) -> "None":
        def handler(
            is_paid: "Annotated[bool, Parameter(query='isPaid')]" = False,
        ) -> "dict[str, Any]":
            return {"is_paid": is_paid}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"isPaid": "is_paid"}

    def test_query_matching_python_name_is_omitted(self) -> "None":
        def handler(
            page: "Annotated[int, Parameter(query='page')]" = 1,
        ) -> "dict[str, Any]":
            return {"page": page}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_header_alias_ignored(self) -> "None":
        def handler(
            tenant_id: "Annotated[str, Parameter(header='X-Tenant')]" = "",
        ) -> "dict[str, Any]":
            return {"tenant_id": tenant_id}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_query_parameter_name_alias_is_included(self) -> "None":
        def handler(
            category_name_in: "Annotated[list[str] | None, QueryParameter(name='categoryNameIn')]" = None,
        ) -> "dict[str, Any]":
            return {"category_name_in": category_name_in}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"categoryNameIn": "category_name_in"}

    def test_parameter_kwarg_name_alias_is_included(self) -> "None":
        def handler(
            category_name_in: "Annotated[list[str] | None, ParameterKwarg(name='categoryNameIn')]" = None,
        ) -> "dict[str, Any]":
            return {"category_name_in": category_name_in}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"categoryNameIn": "category_name_in"}

    def test_header_parameter_name_alias_ignored(self) -> "None":
        def handler(
            tenant_id: "Annotated[str, HeaderParameter(name='X-Tenant')]" = "",
        ) -> "dict[str, Any]":
            return {"tenant_id": tenant_id}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_cookie_parameter_name_alias_ignored(self) -> "None":
        def handler(
            session_id: "Annotated[str, CookieParameter(name='session_id_cookie')]" = "",
        ) -> "dict[str, Any]":
            return {"session_id": session_id}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}

    def test_dependency_provider_query_parameter_name_alias(self) -> "None":
        def provide_filter(
            category_name_in: "Annotated[list[str] | None, QueryParameter(name='categoryNameIn')]" = None,
        ) -> "list[str]":
            return category_name_in or []

        def handler(filter_data: "list[str]") -> "dict[str, Any]":
            return {"data": filter_data}

        _, h = create_app_with_handler(
            handler,
            route_path="/items",
            dependencies={"filter_data": Provide(provide_filter, sync_to_thread=False)},
        )
        assert parameter_aliases(h) == {"categoryNameIn": "category_name_in"}

    def test_dependency_provider_header_and_cookie_parameter_ignored(self) -> "None":
        def provide_context(
            auth_header: "Annotated[str, HeaderParameter(name='Authorization')]" = "",
            cookie_val: "Annotated[str, CookieParameter(name='session')]" = "",
        ) -> "str":
            return auth_header or cookie_val

        def handler(ctx: "str") -> "dict[str, Any]":
            return {"ctx": ctx}

        _, h = create_app_with_handler(
            handler,
            route_path="/items",
            dependencies={"ctx": Provide(provide_context, sync_to_thread=False)},
        )
        assert parameter_aliases(h) == {}

    def test_no_query_omitted_even_when_annotated(self) -> "None":
        def handler(
            n: "Annotated[int, Parameter(description='count')]" = 0,
        ) -> "dict[str, Any]":
            return {"n": n}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}


# ==============================================================================
# 2. Handler Signature Extraction Coverage (from test_handler_signature.py)
# ==============================================================================


class TestHandlerSignature:
    def test_extract_advertised_handler_arguments_filters_di_reserved_and_path_params(self) -> "None":
        async def provide_secret() -> "str":
            return "secret"

        def handler(
            item_id: "str",
            request: "Request[Any, Any, Any]",
            secret: "str",
            q: "str",
            limit: "int" = 10,
        ) -> "dict[str, Any]":
            return {"item_id": item_id, "q": q, "limit": limit, "secret": secret, "path": request.url.path}

        _, route_handler = create_app_with_handler(
            handler,
            route_path="/items/{item_id:str}",
            mcp_prompt="item_prompt",
            dependencies={"secret": Provide(provide_secret)},
        )

        args = extract_advertised_handler_arguments(route_handler, path_parameters={"item_id"})
        assert [(arg["name"], arg["required"]) for arg in args] == [("q", True), ("limit", False)]

    def test_extract_advertised_handler_arguments_uses_query_alias_and_docstring_description(self) -> "None":
        def handler(
            is_paid: "Annotated[bool, Parameter(query='isPaid')]",
            page_size: "Annotated[int, Parameter(query='pageSize')]" = 50,
        ) -> "dict[str, Any]":
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

    def test_extract_advertised_handler_arguments_filters_litestar_signature_namespace(self) -> "None":
        def handler(headers: "dict[str, str]", text: "str") -> "dict[str, str]":
            return {"text": text, "headers": str(headers)}

        _, route_handler = create_app_with_handler(handler)
        args = extract_advertised_handler_arguments(route_handler)
        assert [arg["name"] for arg in args] == ["text"]

    def test_extract_advertised_handler_arguments_filters_dishka_resolved_provider_params(self) -> "None":
        from dishka import Provider, Scope, make_async_container, provide
        from dishka.integrations.litestar import LitestarProvider, setup_dishka
        from litestar import Litestar, get
        from litestar.params import FromQuery

        from tests.unit.conftest import get_handler_from_app

        class DishkaProvider(Provider):
            scope = Scope.REQUEST

            @provide
            def driver(self) -> "_IntrospectionDriver":
                return _IntrospectionDriver()

        async def provide_task_service(driver: "_IntrospectionDriver") -> "_IntrospectionTaskService":
            return _IntrospectionTaskService()

        @get(
            "/hello",
            opt={"mcp_tool": "hello"},
            dependencies={"task_service": Provide(provide_task_service)},
            sync_to_thread=False,
        )
        def hello(name: "FromQuery[str]") -> "dict[str, str]":
            return {"hello": name}

        app = Litestar(route_handlers=[hello])
        container = make_async_container(LitestarProvider(), DishkaProvider())
        setup_dishka(container=container, app=app)
        route_handler = get_handler_from_app(app, "/hello")

        args = extract_advertised_handler_arguments(route_handler)
        assert [arg["name"] for arg in args] == ["name"]

    def test_import_iter_dependency_input_parameters(self) -> "None":
        from litestar_mcp.utils.handler_signature import iter_dependency_input_parameters

        # A simple test to verify it works
        def handler(q: "str") -> "None":
            pass

        _, route_handler = create_app_with_handler(handler)
        params = iter_dependency_input_parameters(route_handler)
        assert isinstance(params, list)

    def test_import_parameter_aliases(self) -> "None":
        from litestar_mcp.utils.handler_signature import parameter_aliases

        def handler(q: "Annotated[str, Parameter(query='query_alias')]") -> "None":
            pass

        _, route_handler = create_app_with_handler(handler)
        aliases = parameter_aliases(route_handler)
        assert aliases == {"query_alias": "q"}


# ==============================================================================
# 3. Description Precedence & Structured Rendering (from test_descriptions.py)
# ==============================================================================


class TestToolDescriptionPrecedence:
    def test_opt_wins_over_decorator_and_docstring(self) -> "None":
        @mcp_tool("foo", description="from-decorator")
        @get("/", opt={"mcp_description": "from-opt"})
        def handler() -> "str":
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-opt"

    def test_decorator_wins_over_docstring(self) -> "None":
        @mcp_tool("foo", description="from-decorator")
        @get("/")
        def handler() -> "str":
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-decorator"

    def test_docstring_wins_over_fallback(self) -> "None":
        @mcp_tool("foo")
        @get("/")
        def handler() -> "str":
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-docstring."

    def test_fallback_when_nothing_set(self) -> "None":
        @mcp_tool("foo")
        @get("/")
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "Tool: foo"

    def test_empty_string_treated_as_absent(self) -> "None":
        @mcp_tool("foo", description="")
        @get("/", opt={"mcp_description": ""})
        def handler() -> "str":
            """from-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo") == "from-docstring."

    def test_route_handler_description_wins_over_docstring_and_loses_to_mcp_overrides(self) -> "None":
        @get("/items", description="Clean REST route description.", opt={"mcp_tool": "list_items"})
        def handler_with_route_desc() -> "list[str]":
            """Internal docstring that should not be used."""
            return []

        fn1 = get_handler_function(handler_with_route_desc)
        assert (
            render_description(handler_with_route_desc, fn1, kind="tool", fallback_name="list_items")
            == "Clean REST route description."
        )

        @mcp_tool("list_items", description="Decorator MCP description.")
        @get("/items", description="Clean REST route description.")
        def handler_with_decorator() -> "list[str]":
            """Internal docstring."""
            return []

        fn2 = get_handler_function(handler_with_decorator)
        assert (
            render_description(handler_with_decorator, fn2, kind="tool", fallback_name="list_items")
            == "Decorator MCP description."
        )

        @mcp_tool("list_items", description="Decorator MCP description.")
        @get("/items", description="Clean REST route description.", opt={"mcp_description": "Opt MCP description."})
        def handler_with_opt() -> "list[str]":
            """Internal docstring."""
            return []

        fn3 = get_handler_function(handler_with_opt)
        assert (
            render_description(handler_with_opt, fn3, kind="tool", fallback_name="list_items") == "Opt MCP description."
        )

    def test_docstring_fallback_strips_google_sphinx_and_numpy_sections(self) -> "None":
        google_doc = (
            "List active inventory items.\n\n"
            "Filters by warehouse and status.\n\n"
            "Args:\n"
            "    service: Injected inventory service.\n"
            "    request: Active HTTP request.\n"
            "    limit: Maximum items to return.\n\n"
            "Returns:\n"
            "    Matching inventory items.\n"
        )
        assert strip_docstring_sections(google_doc) == (
            "List active inventory items.\n\nFilters by warehouse and status."
        )

        sphinx_doc = (
            "Fetch user profile.\n\n"
            ":param user_id: Identifier of the user.\n"
            ":param db_session: Injected database session.\n"
            ":returns: User profile dictionary.\n"
        )
        assert strip_docstring_sections(sphinx_doc) == "Fetch user profile."

        numpy_doc = (
            "Compute summary metrics.\n\n"
            "Parameters\n"
            "----------\n"
            "window : int\n"
            "    Lookback window in days.\n"
            "service : MetricsService\n"
            "    Injected metrics service.\n"
        )
        assert strip_docstring_sections(numpy_doc) == "Compute summary metrics."

        @get("/inventory", opt={"mcp_tool": "list_inventory"})
        def list_inventory(limit: "int" = 10) -> "list[str]":
            """List active inventory items.

            Args:
                service: Injected inventory service.
                limit: Maximum items to return.
            """
            return []

        fn = get_handler_function(list_inventory)
        rendered = render_description(list_inventory, fn, kind="tool", fallback_name="list_inventory")
        assert rendered == "List active inventory items."
        assert "service" not in rendered
        assert "Args:" not in rendered

    def test_validate_tool_descriptions_enforces_length_bounds(self) -> "None":
        tools = [
            {
                "name": "bad_tool",
                "description": "x" * 1001,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "q" * 751},
                    },
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "s" * 301},
                    },
                },
            }
        ]
        violations = validate_tool_descriptions(tools)
        assert len(violations) == 3
        assert any("bad_tool" in v and "1000" in v for v in violations)
        assert any("query" in v and "750" in v for v in violations)
        assert any("summary" in v and "300" in v for v in violations)

    @pytest.mark.parametrize(
        ("input_schema", "output_schema", "expected_violation"),
        [
            (
                {
                    "type": "object",
                    "properties": {
                        "opt_field": {
                            "anyOf": [{"type": "string", "description": "p" * 751}, {"type": "null"}],
                        }
                    },
                },
                None,
                "Tool 't' parameter 'opt_field' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "one_of_field": {
                            "oneOf": [{"type": "integer"}, {"type": "string", "description": "p" * 751}],
                        }
                    },
                },
                None,
                "Tool 't' parameter 'one_of_field' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "all_of_field": {
                            "allOf": [{"type": "string", "description": "p" * 751}],
                        }
                    },
                },
                None,
                "Tool 't' parameter 'all_of_field' description length 751 exceeds maximum 750",
            ),
            (
                {"anyOf": [{"type": "object", "description": "p" * 751}]},
                None,
                "Tool 't' parameter 'anyOf[0]' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {"doc_child": {"$ref": "#/$defs/DocNode"}},
                    "$defs": {"DocNode": {"type": "object", "description": "p" * 751, "properties": {}}},
                },
                None,
                "Tool 't' parameter 'doc_child' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {"doc_list": {"type": "array", "items": {"$ref": "#/$defs/DocNode"}}},
                    "$defs": {"DocNode": {"type": "object", "description": "p" * 751, "properties": {}}},
                },
                None,
                "Tool 't' parameter 'doc_list.[]' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "ref_items": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/ShortNode", "description": "p" * 751},
                        }
                    },
                    "$defs": {"ShortNode": {"type": "object", "properties": {}}},
                },
                None,
                "Tool 't' parameter 'ref_items.[]' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {},
                    "$defs": {"OrphanEnum": {"type": "string", "enum": ["a", "b"], "description": "p" * 751}},
                },
                None,
                "Tool 't' parameter 'OrphanEnum' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "mapping": {
                            "type": "object",
                            "additionalProperties": {"type": "string", "description": "p" * 751},
                        }
                    },
                },
                None,
                "Tool 't' parameter 'mapping.*' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "strict_map": {
                            "type": "object",
                            "unevaluatedProperties": {"type": "string", "description": "p" * 751},
                        }
                    },
                },
                None,
                "Tool 't' parameter 'strict_map.*' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "tup": {
                            "type": "array",
                            "prefixItems": [{"type": "string", "description": "p" * 751}, {"type": "integer"}],
                        }
                    },
                },
                None,
                "Tool 't' parameter 'tup.[0]' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "legacy_tup": {
                            "type": "array",
                            "items": [{"type": "integer"}, {"type": "string", "description": "p" * 751}],
                        }
                    },
                },
                None,
                "Tool 't' parameter 'legacy_tup.[1]' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "nested_root": {
                            "$ref": "#/$defs/LocalRoot",
                            "$defs": {
                                "LocalRoot": {
                                    "type": "object",
                                    "properties": {"inner_field": {"type": "string", "description": "p" * 751}},
                                }
                            },
                        }
                    },
                },
                None,
                "Tool 't' parameter 'nested_root.inner_field' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "legacy": {"$ref": "#/definitions/LegacyNode"},
                        "escaped": {"$ref": "#/$defs/ns~1name~0v1"},
                    },
                    "definitions": {
                        "LegacyNode": {
                            "type": "object",
                            "properties": {"field": {"type": "string", "description": "p" * 751}},
                        }
                    },
                    "$defs": {
                        "ns/name~v1": {
                            "type": "object",
                            "properties": {"ok": {"type": "string", "description": "short"}},
                        }
                    },
                },
                None,
                "Tool 't' parameter 'legacy.field' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {"escaped": {"$ref": "#/$defs/ns~1name~0v1"}},
                    "$defs": {
                        "ns/name~v1": {
                            "type": "object",
                            "properties": {"field": {"type": "string", "description": "p" * 751}},
                        }
                    },
                },
                None,
                "Tool 't' parameter 'escaped.field' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "headers": {
                            "type": "object",
                            "patternProperties": {
                                "^x-": {
                                    "type": "object",
                                    "description": "p" * 751,
                                    "properties": {"nested": {"type": "string", "description": "short"}},
                                }
                            },
                        }
                    },
                },
                None,
                "Tool 't' parameter 'headers.^x-' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "payment": {
                            "type": "object",
                            "dependentSchemas": {
                                "credit_card": {
                                    "type": "object",
                                    "properties": {"billing_address": {"type": "string", "description": "p" * 751}},
                                }
                            },
                        }
                    },
                },
                None,
                "Tool 't' parameter 'payment.billing_address' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "arr": {"type": "array", "contains": {"type": "string", "description": "p" * 751}},
                    },
                },
                None,
                "Tool 't' parameter 'arr.[]' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "dict_keys": {
                            "type": "object",
                            "propertyNames": {"type": "string", "description": "p" * 751},
                        }
                    },
                },
                None,
                "Tool 't' parameter 'dict_keys.<propertyNames>' description length 751 exceeds maximum 750",
            ),
            (
                {
                    "type": "object",
                    "properties": {
                        "cond": {
                            "if": {"type": "object"},
                            "then": {
                                "type": "object",
                                "properties": {"branch_prop": {"type": "string", "description": "p" * 751}},
                            },
                            "else": {"not": {"type": "string", "description": "short"}},
                        }
                    },
                },
                None,
                "Tool 't' parameter 'cond.branch_prop' description length 751 exceeds maximum 750",
            ),
            (
                None,
                {
                    "type": "object",
                    "properties": {
                        "opt_out": {
                            "anyOf": [{"type": "string", "description": "o" * 301}, {"type": "null"}],
                        }
                    },
                },
                "Tool 't' output field 'opt_out' description length 301 exceeds maximum 300",
            ),
        ],
    )
    def test_validate_tool_descriptions_recursive_schema_constructs(
        self,
        input_schema: "dict[str, Any] | None",
        output_schema: "dict[str, Any] | None",
        expected_violation: "str",
    ) -> "None":
        entry: dict[str, Any] = {"name": "t", "description": "Short."}
        if input_schema is not None:
            entry["inputSchema"] = input_schema
        if output_schema is not None:
            entry["outputSchema"] = output_schema
        assert validate_tool_descriptions([entry]) == [expected_violation]

    def test_validate_tool_descriptions_mutual_cycles_diamonds_and_valid_root_summary(self) -> "None":
        long_param = "p" * 751
        cyclic_and_diamond = {
            "name": "graph_tool",
            "description": "Valid.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start": {"$ref": "#/$defs/NodeA"},
                    "first": {"$ref": "#/$defs/Shared"},
                    "second": {"$ref": "#/$defs/Shared"},
                },
                "$defs": {
                    "NodeA": {
                        "type": "object",
                        "properties": {"b": {"$ref": "#/$defs/NodeB"}},
                    },
                    "NodeB": {
                        "type": "object",
                        "properties": {
                            "a": {"$ref": "#/$defs/NodeA"},
                            "leaf": {"type": "string", "description": long_param},
                        },
                    },
                    "Shared": {
                        "type": "object",
                        "properties": {"val": {"type": "string", "description": long_param}},
                    },
                },
            },
        }
        violations = validate_tool_descriptions([cyclic_and_diamond])
        assert "Tool 'graph_tool' parameter 'start.b.leaf' description length 751 exceeds maximum 750" in violations
        assert "Tool 'graph_tool' parameter 'first.val' description length 751 exceeds maximum 750" in violations
        assert len(violations) == 2

    def test_validate_tool_descriptions_expands_cross_referenced_and_self_containing_schemas_once(self) -> "None":
        names = [f"Node{index}" for index in range(14)]
        defs: dict[str, Any] = {
            owner: {
                "type": "object",
                "properties": {other.lower(): {"$ref": f"#/$defs/{other}"} for other in names if other != owner},
            }
            for owner in names
        }
        defs["Node13"]["properties"]["leaf"] = {"type": "string", "description": "p" * 751}
        self_containing: dict[str, Any] = {"type": "object", "properties": {}}
        self_containing["properties"]["again"] = self_containing
        tool = {
            "name": "mesh_tool",
            "description": "Valid.",
            "inputSchema": {
                "type": "object",
                "properties": {"root": {"$ref": "#/$defs/Node0"}, "loop": self_containing},
                "$defs": defs,
            },
        }
        leaf_path = ".".join(["root", *(f"node{index}" for index in range(1, 14)), "leaf"])
        assert validate_tool_descriptions([tool]) == [
            f"Tool 'mesh_tool' parameter '{leaf_path}' description length 751 exceeds maximum 750"
        ]

    def test_validate_tool_descriptions_skips_bounds_set_to_none(self) -> "None":
        tool = {
            "name": "unbounded_tool",
            "description": "d" * 1001,
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string", "description": "p" * 751}}},
            "outputSchema": {"type": "object", "properties": {"r": {"type": "string", "description": "o" * 301}}},
        }
        assert (
            validate_tool_descriptions(
                [tool],
                max_tool_description_length=None,
                max_param_description_length=None,
                max_output_field_description_length=None,
            )
            == []
        )

        class RootSummaryOut(msgspec.Struct):
            ok: str

        RootSummaryOut.__doc__ = "r" * 400

        @get("/valid-long-doc", description="d" * 900, mcp_tool="valid_long_doc", sync_to_thread=False)
        def valid_long_doc(q: str) -> RootSummaryOut:
            return RootSummaryOut(ok=q)

        in_schema = generate_schema_for_handler(valid_long_doc)
        out_schema = generate_output_schema_for_handler(valid_long_doc)
        assert len(in_schema["description"]) > 750
        assert out_schema is not None
        assert len(out_schema["description"]) > 300
        assert (
            validate_tool_descriptions(
                [
                    {
                        "name": "valid_long_doc",
                        "description": "d" * 900,
                        "inputSchema": in_schema,
                        "outputSchema": out_schema,
                    }
                ]
            )
            == []
        )

    def test_validate_tool_descriptions_real_msgspec_dataclass_and_typeddict_models(self) -> "None":
        long_in = "a" * 760
        long_out = "b" * 310

        class DocLeaf(msgspec.Struct):
            code: int

        DocLeaf.__doc__ = long_in

        class NestedPayload(msgspec.Struct):
            opt_text: Annotated[str, msgspec.Meta(description=long_in)] | None = None
            doc_leaf: DocLeaf | None = None
            labels: dict[str, Annotated[str, msgspec.Meta(description=long_in)]] = {}
            pair: tuple[Annotated[str, msgspec.Meta(description=long_in)], int] = ("x", 1)

        @dataclass
        class DataclassFilter:
            opt_query: Annotated[str, Parameter(description=long_in)] | None = None

        class TypedOutput(TypedDict):
            opt_detail: Annotated[str, msgspec.Meta(description=long_out)] | None

        @post("/models", mcp_tool="model_tool", opt={"mcp_output_schema": True}, sync_to_thread=False)
        def model_tool(payload: NestedPayload, dc_filter: DataclassFilter) -> TypedOutput:
            raise NotImplementedError

        violations = validate_tool_descriptions(
            [
                {
                    "name": "model_tool",
                    "description": "Short.",
                    "inputSchema": generate_schema_for_handler(model_tool),
                    "outputSchema": generate_output_schema_for_handler(model_tool),
                }
            ]
        )
        assert "Tool 'model_tool' parameter 'payload.opt_text' description length 760 exceeds maximum 750" in violations
        assert "Tool 'model_tool' parameter 'payload.doc_leaf' description length 760 exceeds maximum 750" in violations
        assert "Tool 'model_tool' parameter 'payload.labels.*' description length 760 exceeds maximum 750" in violations
        assert "Tool 'model_tool' parameter 'payload.pair.[0]' description length 760 exceeds maximum 750" in violations
        assert (
            "Tool 'model_tool' parameter 'dc_filter.opt_query' description length 760 exceeds maximum 750" in violations
        )
        assert "Tool 'model_tool' output field 'opt_detail' description length 310 exceeds maximum 300" in violations


class TestStructuredRendering:
    def test_plain_when_no_structured_fields(self) -> "None":
        @mcp_tool("foo", description="simple")
        @get("/")
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert result == "simple"
        assert "##" not in result

    def test_sections_when_structured_fields_set(self) -> "None":
        @mcp_tool(
            "foo",
            description="Do the thing.",
            when_to_use="When the user asks for a thing.",
            returns="A Thing struct.",
            agent_instructions="Never do this without confirming.",
        )
        @get("/")
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert result.startswith("Do the thing.")
        assert "## When to use\nWhen the user asks for a thing." in result
        assert "## Returns\nA Thing struct." in result
        assert "## Instructions\nNever do this without confirming." in result
        assert result.index("## When to use") < result.index("## Returns") < result.index("## Instructions")

    def test_partial_structured_fields(self) -> "None":
        @mcp_tool("foo", description="Do.", when_to_use="Sometimes.")
        @get("/")
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert "## When to use\nSometimes." in result
        assert "## Returns" not in result
        assert "## Instructions" not in result

    def test_tool_decorator_retains_explicit_input_schema_and_task_preflight(self) -> "None":
        input_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"region": {"type": "string"}},
            "required": ["region"],
        }

        @mcp_tool(
            "regional_task",
            input_schema=input_schema,
            task_support="required",
            task_input_before_start=True,
        )
        def handler(region: "str") -> "str":
            return region

        from litestar_mcp.utils import get_mcp_metadata

        metadata = get_mcp_metadata(handler)
        assert metadata is not None
        assert metadata["input_schema"] == input_schema
        assert metadata["task_support"] == "required"
        assert metadata["task_input_before_start"] is True

    def test_structured_false_ignores_structured_fields(self) -> "None":
        @mcp_tool("foo", description="Do.", when_to_use="Sometimes.")
        @get("/")
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo", structured=False)
        assert result == "Do."
        assert "##" not in result

    def test_opt_form_structured_fields(self) -> "None":
        @get(
            "/",
            opt={
                "mcp_description": "opt-prose",
                "mcp_when_to_use": "opt-wtu",
                "mcp_returns": "opt-ret",
                "mcp_agent_instructions": "opt-ai",
            },
        )
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert result.startswith("opt-prose")
        assert "## When to use\nopt-wtu" in result
        assert "## Returns\nopt-ret" in result
        assert "## Instructions\nopt-ai" in result


class TestResourceDescriptionPrecedence:
    def test_opt_key_is_mcp_resource_description(self) -> "None":
        @mcp_resource("bar", description="decorator-desc")
        @get("/", opt={"mcp_resource_description": "opt-desc"})
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "opt-desc"

    def test_resource_docstring_wins_over_fallback(self) -> "None":
        @mcp_resource("bar")
        @get("/")
        def handler() -> "str":
            """res-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "res-docstring."

    def test_resource_fallback(self) -> "None":
        @mcp_resource("bar")
        @get("/")
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "Resource: bar"

    def test_resource_opt_description_key_does_not_apply_to_resources(self) -> "None":
        """``mcp_description`` is the tool key; resources use ``mcp_resource_description``."""

        @mcp_resource("bar")
        @get("/", opt={"mcp_description": "wrong-key"})
        def handler() -> "str":
            """res-docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar") == "res-docstring."


class TestCustomOptKeys:
    """Downstream apps can rename opt keys via ``MCPConfig.opt_keys``."""

    def test_renamed_tool_description_opt_key_is_honoured(self) -> "None":
        from litestar_mcp.mcp.config import MCPOptKeys

        opt_keys = MCPOptKeys(description="x_mcp_description")

        @mcp_tool("foo")
        @get("/", opt={"x_mcp_description": "opt-prose"})
        def handler() -> "str":
            """Docstring."""
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="tool", fallback_name="foo", opt_keys=opt_keys) == "opt-prose"
        # Default opt key is ignored when renamed
        assert opt_keys.description != "mcp_description"
        default_result = render_description(handler, fn, kind="tool", fallback_name="foo")
        assert default_result == "Docstring."

    def test_renamed_resource_description_opt_key(self) -> "None":
        from litestar_mcp.mcp.config import MCPOptKeys

        opt_keys = MCPOptKeys(resource_description="x_mcp_resource_description")

        @mcp_resource("bar")
        @get("/", opt={"x_mcp_resource_description": "opt-res"})
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        assert render_description(handler, fn, kind="resource", fallback_name="bar", opt_keys=opt_keys) == "opt-res"

    def test_renamed_structured_field_opt_keys(self) -> "None":
        from litestar_mcp.mcp.config import MCPOptKeys

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
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        result = render_description(handler, fn, kind="tool", fallback_name="foo", opt_keys=opt_keys)
        assert result.startswith("d")
        assert "## When to use\nwtu" in result
        assert "## Returns\nret" in result
        assert "## Instructions\nai" in result


class TestExtractDescriptionSources:
    def test_returns_structured_dataclass(self) -> "None":
        @mcp_tool("foo", description="d", when_to_use="wtu", returns="r", agent_instructions="ai")
        @get("/")
        def handler() -> "str":
            return ""

        fn = get_handler_function(handler)
        sources = extract_description_sources(handler, fn, kind="tool", fallback_name="foo")
        assert sources.description == "d"
        assert sources.when_to_use == "wtu"
        assert sources.returns == "r"
        assert sources.agent_instructions == "ai"

    def test_missing_structured_fields_are_none(self) -> "None":
        @mcp_tool("foo", description="d")
        @get("/")
        def handler() -> "str":
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
    def _make_app(*handlers: "Any") -> "Litestar":
        return Litestar(route_handlers=list(handlers), plugins=[LitestarMCP(MCPConfig())])

    @staticmethod
    def _rpc(client: "TestClient[Any]", method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
        data: dict[str, Any] = mcp_post(client, method, params).json()
        return data

    def test_tools_list_returns_decorator_description(self) -> "None":
        @mcp_tool("t", description="LLM prose", when_to_use="When asked")
        @get("/x", sync_to_thread=False)
        def handler() -> "dict[str, Any]":
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            result = self._rpc(client, "tools/list")
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr.startswith("LLM prose")
            assert "## When to use\nWhen asked" in descr

    def test_tools_list_opt_form_overrides_docstring(self) -> "None":
        @get("/x", opt={"mcp_tool": "t", "mcp_description": "opt-prose"}, sync_to_thread=False)
        def handler() -> "dict[str, Any]":
            """docstring-prose."""
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            result = self._rpc(client, "tools/list")
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr == "opt-prose"

    def test_tools_list_docstring_fallback_unchanged(self) -> "None":
        """v0.4.0 regression guard: plain docstring → unchanged plain string."""

        @get("/x", opt={"mcp_tool": "t"}, sync_to_thread=False)
        def handler() -> "dict[str, Any]":
            """plain-docstring."""
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            result = self._rpc(client, "tools/list")
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr == "plain-docstring."
            assert "##" not in descr

    def test_resources_list_returns_rendered_description(self) -> "None":
        @mcp_resource("r", description="res-prose", when_to_use="Sometimes")
        @get("/y", sync_to_thread=False)
        def handler() -> "dict[str, Any]":
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            result = self._rpc(client, "resources/list")
            resources = result["result"]["resources"]
            descr = next(r["description"] for r in resources if r["name"] == "r")
            assert descr.startswith("res-prose")
            assert "## When to use\nSometimes" in descr

    def test_resources_list_opt_form_resource_description_key(self) -> "None":
        @get(
            "/y",
            opt={"mcp_resource": "r", "mcp_resource_description": "opt-res-prose"},
            sync_to_thread=False,
        )
        def handler() -> "dict[str, Any]":
            """docstring-prose."""
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            result = self._rpc(client, "resources/list")
            resources = result["result"]["resources"]
            descr = next(r["description"] for r in resources if r["name"] == "r")
            assert descr == "opt-res-prose"

    def test_rendered_tool_description_contains_structured_sections(self) -> "None":
        @mcp_tool("t", description="primary", when_to_use="wtu", returns="r")
        @get("/x", sync_to_thread=False)
        def handler() -> "dict[str, Any]":
            return {}

        with TestClient(app=self._make_app(handler)) as client:
            tl = self._rpc(client, "tools/list")
            tl_descr = next(t["description"] for t in tl["result"]["tools"] if t["name"] == "t")

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
    def test_tools_list_docstring_stripped_like_legacy(self, doc: "str") -> "None":
        """Plain-mode output must equal legacy ``(fn.__doc__ or ...).strip()``."""

        @get("/x", opt={"mcp_tool": "t"}, sync_to_thread=False)
        def handler() -> "dict[str, Any]":
            return {}

        handler.fn.__doc__ = doc

        with TestClient(app=self._make_app(handler)) as client:
            result = self._rpc(client, "tools/list")
            tools = result["result"]["tools"]
            descr = next(t["description"] for t in tools if t["name"] == "t")
            assert descr == doc.strip()
