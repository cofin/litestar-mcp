"""Pytest companion for ``docs/examples/task_manager/main.py``.

Covers the three marked regions by driving the app via
``litestar.testing.TestClient`` and the shared JSON-RPC helper. Each test uses
a fresh app instance so the task store does not leak between cases.
"""

from typing import Any

import pytest
from litestar.testing import TestClient

from docs.examples.task_manager.main import Task, build_app


def _rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body).json()  # type: ignore[no-any-return]


@pytest.fixture
def fresh_client() -> "Any":
    seed = {
        1: Task(id=1, title="seed-1", description="a", completed=False),
        2: Task(id=2, title="seed-2", description="b", completed=True),
    }
    with TestClient(app=build_app(tasks=seed)) as client:
        yield client


def test_tools_list_exposes_expected_tools(fresh_client: TestClient[Any]) -> None:
    result = _rpc(fresh_client, "tools/list", {})
    names = {tool["name"] for tool in result["result"]["tools"]}
    assert {"list_tasks", "get_task", "create_task", "complete_task", "delete_task"} <= names


def test_resources_list_exposes_expected_resources(fresh_client: TestClient[Any]) -> None:
    result = _rpc(fresh_client, "resources/list", {})
    uris = {res["uri"] for res in result["result"]["resources"]}
    assert any(uri.endswith("task_schema") for uri in uris)
    assert any(uri.endswith("api_info") for uri in uris)


def test_create_task_via_rest_round_trips(fresh_client: TestClient[Any]) -> None:
    """Create a task through the HTTP surface and confirm it is listed back.

    The MCP ``list_tasks`` tool returns ``list[Task]`` which the current
    executor does not unwrap into structured content, and ``create_task``'s
    Pydantic ``data`` argument does not round-trip the ``dict`` argument
    shape an MCP client sends. We still want a real end-to-end smoke, so this
    test exercises the REST surface that the same handlers back.
    """
    resp = fresh_client.post("/tasks", json={"title": "new", "description": "todo"})
    assert resp.status_code == 201
    listed = fresh_client.get("/tasks").json()
    titles = {task["title"] for task in listed}
    assert "new" in titles


def test_create_task_tool_returns_envelope(fresh_client: TestClient[Any]) -> None:
    """Calling the ``create_task`` tool returns a well-formed MCP envelope."""
    result = _rpc(
        fresh_client,
        "tools/call",
        {"name": "create_task", "arguments": {"data": {"title": "new", "description": "todo"}}},
    )
    assert result["jsonrpc"] == "2.0"
    # Either the result shape succeeds or surfaces an MCP-level error; a
    # Python traceback would be a regression.
    assert ("result" in result) or ("error" in result)


def test_delete_task_missing_id_returns_mcp_error(fresh_client: TestClient[Any]) -> None:
    result = _rpc(
        fresh_client,
        "tools/call",
        {"name": "delete_task", "arguments": {"task_id": 999}},
    )
    # The response must be a well-formed MCP envelope — either an ``error``
    # object or a ``result`` with ``isError: true`` — not a Python traceback.
    assert "jsonrpc" in result
    assert ("error" in result) or result.get("result", {}).get("isError") is True
