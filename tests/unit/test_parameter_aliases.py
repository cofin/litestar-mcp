"""Tests for the wire-name → python-name alias helper."""

from typing import Annotated, Any

from litestar.params import Parameter

from litestar_mcp.utils.handler_signature import parameter_aliases
from tests.unit.conftest import create_app_with_handler


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
