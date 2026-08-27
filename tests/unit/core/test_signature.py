"""Unit tests for handler parameter reflection and argument resolution."""

from typing import Annotated

from litestar.params import ParameterKwarg

from litestar_mcp.core import (
    get_advertised_handler_parameters,
    get_handler_function,
    parameter_aliases,
    resolve_tool_argument_aliases,
)
from tests.unit.conftest import create_app_with_handler


def sample_tool(
    name: str,
    count: Annotated[int, ParameterKwarg(query="num_items")] = 1,
) -> str:
    """Sample tool for testing signatures.

    Args:
        name: Name of the person.
        count: Number of greetings.
    """
    return name * count


def test_get_handler_function() -> None:
    """Verify extracting underlying callable function."""
    assert get_handler_function(sample_tool) is sample_tool

    _, handler = create_app_with_handler(sample_tool)
    assert get_handler_function(handler) == sample_tool


def test_get_advertised_parameters() -> None:
    """Verify parameters are accurately advertised with annotations and defaults."""
    _, handler = create_app_with_handler(sample_tool)
    params = get_advertised_handler_parameters(handler)
    assert len(params) == 2
    param_map = {p.python_name: p for p in params}

    assert "name" in param_map
    assert param_map["name"].required
    assert param_map["name"].wire_name == "name"

    assert "count" in param_map
    assert not param_map["count"].required
    assert param_map["count"].wire_name == "num_items"


def test_resolve_tool_argument_aliases() -> None:
    """Verify wire-name resolution and parameter aliases."""
    _, handler = create_app_with_handler(sample_tool)
    params = get_advertised_handler_parameters(handler)
    resolved, consumed, present_aliases = resolve_tool_argument_aliases(
        {"name": "test", "num_items": 5},
        params,
    )
    assert resolved == {"name": "test", "num_items": 5}
    assert consumed == {"name", "num_items"}
    assert not present_aliases

    aliases = parameter_aliases(handler)
    assert aliases == {"num_items": "count"}
