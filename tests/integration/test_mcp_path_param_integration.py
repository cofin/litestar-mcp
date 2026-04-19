"""End-to-end JSON-RPC roundtrip for typed path-parameter coercion (GH #43).

Pins that an MCP ``tools/call`` on ``@get("/workspaces/{workspace_id:uuid}/files",
mcp_tool="list_files")`` dispatches with a real ``UUID`` in
``connection.path_params``, not a raw ``str``. Red phase until the executor
calls ``parse_path_params`` on the dispatch scope.
"""

from typing import Any
from uuid import UUID

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from tests.integration.conftest import rpc

pytestmark = pytest.mark.integration


def _build_app(captured: dict[str, Any]) -> Litestar:
    @get(
        "/workspaces/{workspace_id:uuid}/files",
        mcp_tool="list_files",
        sync_to_thread=False,
    )
    def list_files(workspace_id: UUID) -> dict[str, Any]:
        captured["workspace_id"] = workspace_id
        captured["type"] = type(workspace_id).__name__
        return {"files": [], "workspace_id": str(workspace_id)}

    return Litestar(route_handlers=[list_files], plugins=[LitestarMCP()])


def test_tools_call_coerces_uuid_path_param_end_to_end() -> None:
    captured: dict[str, Any] = {}
    app = _build_app(captured)

    with TestClient(app=app) as client:
        response = rpc(
            client,
            "tools/call",
            {
                "name": "list_files",
                "arguments": {"workspace_id": "6bc9e12e-0000-0000-0000-000000000000"},
            },
        )

    assert response["result"]["isError"] is False
    assert isinstance(captured["workspace_id"], UUID)
    assert captured["workspace_id"] == UUID("6bc9e12e-0000-0000-0000-000000000000")
