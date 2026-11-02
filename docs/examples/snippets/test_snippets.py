"""Smoke test runnable snippet modules.

Runnable app snippets expose ``build() -> Litestar`` or a module-level ``app`` variable.
Client-only snippets, configuration snippets exposing a module-level ``config``, and
handler-only fragments are import-smoked separately.
"""

import importlib
import pkgutil

import pytest
from litestar import Litestar

import docs.examples.snippets as snippets_pkg
from litestar_mcp import MCPConfig

CONFIG_SNIPPET_MODULES = {"configuration_stateless"}
HANDLER_ONLY_SNIPPET_MODULES = {"tool_explicit_input_schema", "tool_task_input_before_start"}
NON_APP_SNIPPET_MODULES = CONFIG_SNIPPET_MODULES | HANDLER_ONLY_SNIPPET_MODULES

SNIPPET_MODULES = [
    name
    for _finder, name, _ispkg in pkgutil.iter_modules(snippets_pkg.__path__)
    if not name.startswith("test_") and name not in NON_APP_SNIPPET_MODULES
]


@pytest.mark.parametrize("module_name", SNIPPET_MODULES)
def test_snippet_build_returns_litestar(module_name: "str") -> "None":
    """Import the snippet module and verify it defines or builds a Litestar application."""
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
def test_non_app_snippet_imports(module_name: "str") -> "None":
    """Import handler-only snippets that do not define a Litestar application."""
    importlib.import_module(f"docs.examples.snippets.{module_name}")


@pytest.mark.parametrize("module_name", sorted(CONFIG_SNIPPET_MODULES))
def test_config_snippet_exposes_mcp_config(module_name: "str") -> "None":
    """Import configuration snippets and verify they define a module-level ``MCPConfig``."""
    mod = importlib.import_module(f"docs.examples.snippets.{module_name}")
    assert isinstance(mod.config, MCPConfig)


@pytest.mark.anyio
async def test_durable_workflow_snippet_runs_to_completion() -> None:
    """Run the durable workflow snippet to completion and assert node outputs."""
    import anyio
    from litestar.testing import AsyncTestClient
    from litestar_queues.worker import Worker

    from docs.examples.snippets import durable_workflow

    engine, queue_plugin = durable_workflow.create_engine()
    app = Litestar(plugins=[queue_plugin])
    async with AsyncTestClient(app):
        run_id = await engine.start("metrics_pipeline")
        service = queue_plugin.config.get_service(app.state)
        worker = Worker(service, queue_plugin.config.worker)
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


@pytest.mark.anyio
async def test_mcp_tasks_queue_snippet_initializes() -> None:
    """Verify the distributed MCP Tasks queue snippet builds and initializes correctly."""
    from litestar.testing import AsyncTestClient

    from docs.examples.snippets import mcp_tasks_queue

    app = mcp_tasks_queue.build()
    async with AsyncTestClient(app):
        assert app is not None
