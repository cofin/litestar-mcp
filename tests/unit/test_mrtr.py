"""Multi-round-trip result coverage for every supported MCP primitive."""

from typing import Any, cast

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import (
    LitestarMCP,
    MCPInputRequiredResult,
    get_mcp_request_context,
    mcp_prompt,
    mcp_resource,
    mcp_tool,
)
from tests.unit.conftest import mcp_post


def _rpc(client: TestClient[Any], method: str, params: dict[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", mcp_post(client, method, params).json())


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/call", {"name": "interactive_tool", "arguments": {}}),
        ("resources/read", {"uri": "litestar://interactive_resource"}),
        ("prompts/get", {"name": "interactive_prompt", "arguments": {}}),
    ],
)
def test_mrtr_input_required_and_retry(method: str, params: dict[str, Any]) -> None:
    def interactive() -> MCPInputRequiredResult | dict[str, Any]:
        context = get_mcp_request_context()
        if context.input_responses is None:
            return MCPInputRequiredResult(
                input_requests={"confirm": {"type": "elicitation", "message": "Continue?"}},
                request_state="integrity-protected-state",
            )
        return {"messages": []} if method == "prompts/get" else {"accepted": context.input_responses["confirm"]}

    def interactive_tool() -> MCPInputRequiredResult | dict[str, Any]:
        return interactive()

    def interactive_resource() -> MCPInputRequiredResult | dict[str, Any]:
        return interactive()

    tool = mcp_tool(name="interactive_tool")(get("/tool", sync_to_thread=False)(interactive_tool))
    resource = mcp_resource(name="interactive_resource")(get("/resource", sync_to_thread=False)(interactive_resource))

    @mcp_prompt(name="interactive_prompt")
    def prompt() -> MCPInputRequiredResult | dict[str, Any]:
        return interactive()

    app = Litestar(route_handlers=[tool, resource], plugins=[LitestarMCP(prompts=[prompt])])
    with TestClient(app=app) as client:
        first = _rpc(client, method, params)["result"]
        retry_params = {
            **params,
            "inputResponses": {"confirm": True},
            "requestState": first["requestState"],
        }
        second = _rpc(client, method, retry_params)["result"]

    assert first["resultType"] == "input_required"
    assert first["requestState"] == "integrity-protected-state"
    assert second["resultType"] == "complete"
