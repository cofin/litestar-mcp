"""Unit tests for Tool, tool decorator, ToolContext, and execute_tool_calls."""

import asyncio
from typing import Literal

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
