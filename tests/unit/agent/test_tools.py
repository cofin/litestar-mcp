from typing import Any

import pytest

from litestar_mcp.agent.security import ToolContext
from litestar_mcp.agent.tools import execute_tools_in_parallel, tool


@tool(description="Multiply two integers.")
def multiply(a: int, b: int) -> int:
    return a * b


@tool(description="Lookup user profile with context.")
def get_user_profile(user_id: str, context: ToolContext) -> dict[str, Any]:
    return {"user_id": user_id, "tenant": context.tenant_id, "user": context.user}


def test_tool_parameter_stripping() -> None:
    schema = multiply.to_json_schema()
    assert schema["name"] == "multiply"
    assert "a" in schema["parameters"]["properties"]
    assert "b" in schema["parameters"]["properties"]

    profile_schema = get_user_profile.to_json_schema()
    assert "user_id" in profile_schema["parameters"]["properties"]
    assert "context" not in profile_schema["parameters"]["properties"]
    assert "context" in get_user_profile.stripped_parameters


@pytest.mark.asyncio
async def test_tool_invocation_with_context() -> None:
    ctx = ToolContext(tenant_id="tenant-123", user="admin")
    res = await get_user_profile(user_id="user-456", __tool_context__=ctx)
    assert res["user_id"] == "user-456"
    assert res["tenant"] == "tenant-123"
    assert res["user"] == "admin"


@pytest.mark.asyncio
async def test_execute_tools_in_parallel() -> None:
    tools_map = {"multiply": multiply, "get_user_profile": get_user_profile}
    calls = [
        {"name": "multiply", "call_id": "c1", "arguments": {"a": 3, "b": 4}},
        {"name": "get_user_profile", "call_id": "c2", "arguments": {"user_id": "u1"}},
    ]
    ctx = ToolContext(tenant_id="t-99", user="bob")
    results = await execute_tools_in_parallel(calls, tools_map, context=ctx)
    assert len(results) == 2
    assert results[0]["call_id"] == "c1"
    assert results[0]["content"] == "12"
    assert not results[0]["is_error"]
    assert results[1]["call_id"] == "c2"
    assert "tenant-99" in results[1]["content"] or "t-99" in results[1]["content"]
