"""Tests for the wire-name → python-name alias helper."""

from typing import Annotated, Any

from litestar.params import Parameter

from litestar_mcp._parameter_aliases import parameter_aliases
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

    def test_header_alias_is_included(self) -> None:
        def handler(
            user_agent: Annotated[str, Parameter(header="User-Agent")] = "",
        ) -> dict[str, Any]:
            return {"user_agent": user_agent}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"User-Agent": "user_agent"}

    def test_query_takes_precedence_over_header(self) -> None:
        def handler(
            x: Annotated[int, Parameter(query="qX", header="hX")] = 0,
        ) -> dict[str, Any]:
            return {"x": x}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {"qX": "x"}

    def test_no_alias_omitted_even_when_annotated(self) -> None:
        def handler(
            n: Annotated[int, Parameter(description="count")] = 0,
        ) -> dict[str, Any]:
            return {"n": n}

        _, h = create_app_with_handler(handler)
        assert parameter_aliases(h) == {}
