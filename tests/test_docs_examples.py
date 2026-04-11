"""Regression tests for documentation example applications."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from litestar.testing import TestClient

from litestar_mcp.executor import execute_tool
from litestar_mcp.schema_builder import generate_schema_for_handler
from tests.conftest import get_handler_from_app

if TYPE_CHECKING:
    from types import ModuleType


def load_advanced_example_module() -> ModuleType:
    """Load the advanced example module from the docs tree."""

    path = Path(__file__).resolve().parents[1] / "docs/examples/advanced/main.py"
    module_name = "docs_examples_advanced_main"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None

    temp_dir = tempfile.mkdtemp(prefix="litestar-mcp-advanced-example-")
    database_path = Path(temp_dir) / "db.sqlite3"
    previous_database = os.environ.get("LITESTAR_MCP_ADVANCED_DB")
    os.environ["LITESTAR_MCP_ADVANCED_DB"] = str(database_path)

    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_database is None:
            os.environ.pop("LITESTAR_MCP_ADVANCED_DB", None)
        else:
            os.environ["LITESTAR_MCP_ADVANCED_DB"] = previous_database


@pytest.mark.asyncio
async def test_advanced_example_uses_shared_di_for_tool_and_resource() -> None:
    """The advanced example should demonstrate DI in both a tool and a resource."""

    module = load_advanced_example_module()
    app = module.app

    list_tasks_handler = get_handler_from_app(app, "/tasks", "GET")
    api_info_handler = get_handler_from_app(app, "/api/info", "GET")

    assert "task_service" in list_tasks_handler.dependencies
    assert "task_service" in api_info_handler.dependencies

    list_tasks_schema = generate_schema_for_handler(list_tasks_handler)
    api_info_schema = generate_schema_for_handler(api_info_handler)

    assert "task_service" not in list_tasks_schema["properties"]
    assert "completed" in list_tasks_schema["properties"]
    assert "task_service" not in api_info_schema["properties"]

    with TestClient(app=app):
        api_info = await execute_tool(api_info_handler, app, {})

    assert api_info["storage_backend"] == "sqlite"
    assert api_info["tasks_count"] == len(module.INITIAL_TASKS)


def test_advanced_example_docs_use_real_path() -> None:
    """The docs should point at the real docs example locations."""

    examples_page = Path("docs/examples.rst").read_text()
    usage_page = Path("docs/usage/examples.rst").read_text()
    examples_readme = Path("docs/examples/README.md").read_text()
    getting_started = Path("docs/getting-started.rst").read_text()

    assert "docs/examples/advanced/" in examples_page
    assert "docs/examples/advanced/" in usage_page
    assert "cd docs/examples/advanced/" in examples_page
    assert "docs/examples/basic/" in examples_page
    assert "docs/examples/basic/" in usage_page
    assert "cd docs/examples/basic/" in examples_page
    assert "cd docs/examples/basic/" in examples_readme
    assert "docs/examples/basic/" in getting_started
