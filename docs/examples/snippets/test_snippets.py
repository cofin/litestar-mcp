"""Smoke test runnable snippet modules.

Runnable app snippets expose ``build() -> Litestar`` or a module-level ``app`` variable.
Client-only snippets, configuration snippets exposing a module-level ``config``, and
handler-only fragments are import-smoked separately.
"""

import importlib
import pkgutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

import docs.examples.snippets as snippets_pkg
from docs.examples.snippets import mcp_apps_and_structured, skills_prompts_and_catalog
from litestar_mcp import MCPConfig
from litestar_mcp.mcp.routes import MCP_NAME_FIELDS

OPTIONAL_SNIPPET_DEPENDENCIES = {
    "a2a_http_json_multitenant": "a2a",
    "a2a_plugin": "a2a",
    "durable_workflow": "litestar_queues",
    "mcp_tasks_queue": "litestar_queues",
    "protocol_bridges": "a2a",
    "queued_agent_turns": "litestar_queues",
}
CONFIG_SNIPPET_MODULES = {"configuration_stateless"}
HANDLER_ONLY_SNIPPET_MODULES = {"tool_explicit_input_schema", "tool_task_input_before_start"}
NON_APP_SNIPPET_MODULES = CONFIG_SNIPPET_MODULES | HANDLER_ONLY_SNIPPET_MODULES

SNIPPET_MODULES = [
    name
    for _finder, name, _ispkg in pkgutil.iter_modules(snippets_pkg.__path__)
    if not name.startswith("test_") and name not in NON_APP_SNIPPET_MODULES
]


@pytest.mark.parametrize("module_name", SNIPPET_MODULES)
def test_snippet_build_returns_litestar(module_name: str) -> None:
    """Import the snippet module and verify it defines or builds a Litestar application."""
    if module_name in OPTIONAL_SNIPPET_DEPENDENCIES:
        pytest.importorskip(OPTIONAL_SNIPPET_DEPENDENCIES[module_name])
    mod = importlib.import_module(f"docs.examples.snippets.{module_name}")
    if hasattr(mod, "build"):
        app = mod.build()
    elif hasattr(mod, "app"):
        app = mod.app
    elif hasattr(mod, "mcp"):
        app = mod.mcp.app
    else:
        pytest.fail(f"{module_name} has neither build(), app, nor mcp variable")
    assert isinstance(app, Litestar)


@pytest.mark.parametrize("module_name", sorted(HANDLER_ONLY_SNIPPET_MODULES))
def test_non_app_snippet_imports(module_name: str) -> None:
    """Import handler-only snippets that do not define a Litestar application."""
    importlib.import_module(f"docs.examples.snippets.{module_name}")


@pytest.mark.parametrize("module_name", sorted(CONFIG_SNIPPET_MODULES))
def test_config_snippet_exposes_mcp_config(module_name: str) -> None:
    """Import configuration snippets and verify they define a module-level ``MCPConfig``."""
    mod = importlib.import_module(f"docs.examples.snippets.{module_name}")
    assert isinstance(mod.config, MCPConfig)


@pytest.mark.anyio
async def test_durable_workflow_snippet_runs_to_completion() -> None:
    """Start a run through the snippet's route and drive it to completion with a worker."""
    queues = pytest.importorskip("litestar_queues")
    worker_module = importlib.import_module("litestar_queues.worker")
    durable_workflow = importlib.import_module("docs.examples.snippets.durable_workflow")
    engine = importlib.import_module("litestar_mcp.queues").DurableWorkflowEngine([durable_workflow.metrics_pipeline])
    queue_plugin = queues.QueuePlugin(
        queues.QueueConfig(queue_backend="memory", worker=queues.WorkerConfig(placement="external"))
    )
    app = Litestar(route_handlers=[durable_workflow.start_metrics_pipeline], plugins=[queue_plugin, engine])

    async with AsyncTestClient(app) as client:
        response = await client.post("/pipelines/metrics")
        run_id = response.json()["run_id"]
        worker = worker_module.Worker(queue_plugin.get_service(), queue_plugin.config.worker)
        for _ in range(50):
            run, _ = await engine.snapshot(run_id)
            if run.status != "running":
                break
            await worker.run_once()
            await anyio.sleep(0.01)
        await worker.stop()

        run, ctx = await engine.snapshot(run_id)
        assert run.status == "completed"
        assert ctx.node_outputs["extract"]["raw_count"] == 42
        assert ctx.node_outputs["transform"]["processed_count"] == 84


def test_mcp_tasks_queue_snippet_wires_executor() -> None:
    """The distributed MCP Tasks snippet attaches the executor to the app's QueuePlugin."""
    queues = pytest.importorskip("litestar_queues")
    mcp_tasks_queue = importlib.import_module("docs.examples.snippets.mcp_tasks_queue")

    app = mcp_tasks_queue.build()
    resolver = app.plugins.get(queues.QueuePlugin).config.task_dependency_resolver
    assert resolver is not None
    assert resolver.__self__.__class__.__name__ == "QueueTaskExecutor"


async def _post_mcp(
    client: AsyncTestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_params = dict(params or {})
    meta = dict(resolved_params.get("_meta") or {})
    meta.setdefault("io.modelcontextprotocol/protocolVersion", "2026-07-28")
    meta.setdefault("io.modelcontextprotocol/clientCapabilities", {})
    resolved_params["_meta"] = meta
    headers = {
        "Accept": "application/json",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    name_field = MCP_NAME_FIELDS.get(method)
    if name_field is not None and isinstance(resolved_params.get(name_field), str):
        headers["Mcp-Name"] = resolved_params[name_field]
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": resolved_params},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["result"]


@pytest.mark.anyio
async def test_mcp_apps_and_structured_snippet() -> None:
    """Exercise MCP Apps UI discovery, structuredContent tool calls, and ui:// HTML reads."""
    app = mcp_apps_and_structured.build()

    async with AsyncTestClient(app) as client:
        discover = await _post_mcp(client, "server/discover")
        assert discover["capabilities"]["extensions"]["io.modelcontextprotocol/ui"] == {
            "mimeTypes": ["text/html;profile=mcp-app"]
        }

        tools_result = await _post_mcp(client, "tools/list")
        tool = next(entry for entry in tools_result["tools"] if entry["name"] == "report_summary")
        assert tool["title"] == "Report Summary"
        assert tool["outputSchema"]["type"] == "object"
        assert tool["outputSchema"]["properties"]["report_id"]["type"] == "string"
        assert tool["_meta"]["ui"]["resourceUri"] == "ui://reports/view.html"

        call_result = await _post_mcp(
            client,
            "tools/call",
            {"name": "report_summary", "arguments": {"report_id": "q4"}},
        )
        assert call_result["isError"] is False
        assert call_result["structuredContent"] == {
            "report_id": "q4",
            "title": "Report q4",
            "total_items": 12,
            "status": "ready",
        }

        read_result = await _post_mcp(client, "resources/read", {"uri": "ui://reports/view.html"})
        assert read_result["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
        assert read_result["contents"][0]["text"] == '<main data-app="reports-view"><h1>Report Summary</h1></main>'


@pytest.mark.anyio
async def test_skills_prompts_and_catalog_snippet(tmp_path: Path) -> None:
    """Exercise default build() and isolated build(skills_root=tmp_path) for skills prompts and SkillCatalog."""
    default_app = skills_prompts_and_catalog.build()
    async with AsyncTestClient(default_app) as client:
        prompts_result = await _post_mcp(client, "prompts/list")
        assert "hello-skill" in [entry["name"] for entry in prompts_result["prompts"]]

        catalog_response = await client.get("/skills-catalog")
        assert catalog_response.status_code == 200
        assert "hello-skill" in [entry["name"] for entry in catalog_response.json()["skills"]]

    demo_dir = tmp_path / "demo-skill"
    demo_dir.mkdir()
    (demo_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo skill for isolated snippet testing.\ntags:\n  - demo\n---\n\nFollow the demo checklist.\n",
        encoding="utf-8",
    )

    isolated_app = skills_prompts_and_catalog.build(skills_root=tmp_path)
    async with AsyncTestClient(isolated_app) as client:
        prompts_result = await _post_mcp(client, "prompts/list")
        assert [entry["name"] for entry in prompts_result["prompts"]] == ["demo-skill"]

        prompt_result = await _post_mcp(client, "prompts/get", {"name": "demo-skill"})
        assert prompt_result["messages"][0]["role"] == "user"
        assert "Follow the demo checklist." in prompt_result["messages"][0]["content"]["text"]

        catalog_response = await client.get("/skills-catalog")
        assert catalog_response.status_code == 200
        assert catalog_response.json() == {
            "skills": [
                {
                    "name": "demo-skill",
                    "description": "Demo skill for isolated snippet testing.",
                    "tags": ["demo"],
                }
            ]
        }
