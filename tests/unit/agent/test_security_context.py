"""Unit tests for ToolContext, RunContextRegistry, and security context propagation."""

from __future__ import annotations

import anyio
import pytest

from litestar_mcp.agent.context import ToolContext, resolve_tool_context
from litestar_mcp.agent.tools import RunContextRegistry, get_current_tool_context, tool


def test_tool_context_state_and_from_connection() -> None:
    """Verify ToolContext state mutation and construction from an ASGI connection."""

    class MockPrincipal:
        def __init__(self) -> None:
            self.roles = ["admin"]
            self.scopes = ["mcp:tools", "mcp:read"]
            self.tenant_id = "org-42"

    class MockRequest:
        def __init__(self) -> None:
            self.user = MockPrincipal()
            self.auth = {"token": "jwt-xyz"}
            self.scope = {"user": self.user, "auth": self.auth}

    req = MockRequest()
    ctx = ToolContext.from_connection(req, session_id="sess-1", turn_id="turn-9")
    assert ctx.tenant_id == "org-42"
    assert ctx.roles == ["admin"]
    assert ctx.scopes == ["mcp:tools", "mcp:read"]
    assert ctx.session_id == "sess-1"
    assert ctx.turn_id == "turn-9"
    assert ctx.auth == {"token": "jwt-xyz"}

    ctx.set("trace_id", "tr-100")
    assert ctx.get("trace_id") == "tr-100"
    assert ctx.get("missing_key", "fallback") == "fallback"

    resolved = resolve_tool_context(req, session_id="sess-2", turn_id="turn-10")
    assert resolved.session_id == "sess-2"
    assert resolved.turn_id == "turn-10"


@pytest.mark.asyncio
async def test_run_context_registry_concurrent_isolation() -> None:
    """Verify RunContextRegistry isolates ToolContext across concurrent async tasks."""

    @tool(description="Echo tenant id from active context.")
    async def read_tenant(label: str, ctx: ToolContext) -> str:
        await anyio.sleep(0.01)
        active = get_current_tool_context()
        assert active is ctx
        return f"{label}:{ctx.tenant_id}"

    results: dict[str, str] = {}

    async def worker(label: str, tenant: str) -> None:
        ctx = ToolContext(tenant_id=tenant)
        with RunContextRegistry.bound(ctx):
            results[label] = await read_tenant.execute({"label": label}, context=ctx)

    async with anyio.create_task_group() as tg:
        tg.start_soon(worker, "a", "tenant-alpha")
        tg.start_soon(worker, "b", "tenant-beta")

    assert results == {
        "a": "a:tenant-alpha",
        "b": "b:tenant-beta",
    }
