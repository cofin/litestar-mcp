"""Pytest companion for ``docs/examples/skills_dynamic/main.py``."""

from typing import Any, cast

from litestar.testing import TestClient

from docs.examples.skills_dynamic.main import build_app


def _rpc(
    client: "TestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
    headers: "dict[str, str] | None" = None,
) -> "dict[str, Any]":
    """Execute one stateless MCP request with optional caller headers."""
    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": request_params}
    req_headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if "uri" in request_params:
        req_headers["Mcp-Name"] = str(request_params["uri"])
    if headers:
        req_headers.update(headers)
    return cast("dict[str, Any]", client.post("/mcp", json=body, headers=req_headers).json())


def test_anonymous_caller_sees_only_filesystem_skill() -> "None":
    with TestClient(app=build_app()) as client:
        result = _rpc(client, "skills/list")
        names = [entry["frontmatter"]["name"] for entry in result["result"]["skills"]]
        assert names == ["hello-skill"]
        assert result["result"]["cacheScope"] == "private"


def test_platform_group_caller_sees_platform_runbook_and_filesystem_skill() -> "None":
    with TestClient(app=build_app()) as client:
        result = _rpc(
            client,
            "skills/list",
            headers={"X-Workspace-Group": "platform"},
        )
        names = [entry["frontmatter"]["name"] for entry in result["result"]["skills"]]
        assert names == ["hello-skill", "platform-runbook"]


def test_cross_group_skills_get_is_rejected_with_invalid_params() -> "None":
    with TestClient(app=build_app()) as client:
        denied = _rpc(
            client,
            "skills/get",
            {"uri": "skill://platform-runbook/SKILL.md"},
            headers={"X-Workspace-Group": "analytics"},
        )
        assert denied["error"]["code"] == -32602

        allowed = _rpc(
            client,
            "skills/get",
            {"uri": "skill://platform-runbook/SKILL.md"},
            headers={"X-Workspace-Group": "platform"},
        )
        assert allowed["result"]["skill"]["frontmatter"]["name"] == "platform-runbook"


def test_lazy_secondary_file_is_served_via_resources_read() -> "None":
    with TestClient(app=build_app()) as client:
        read_res = _rpc(
            client,
            "resources/read",
            {"uri": "skill://platform-runbook/runbook.md"},
            headers={"X-Workspace-Group": "platform"},
        )
        contents = read_res["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "skill://platform-runbook/runbook.md"
        assert "Incident Runbook" in contents[0]["text"]
