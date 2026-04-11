"""Regression tests for documentation example applications."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from litestar.testing import TestClient

from litestar_mcp.schema_builder import generate_schema_for_handler
from tests.conftest import get_handler_from_app

if TYPE_CHECKING:
    from types import ModuleType


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    msg_id: int = 1,
    base: str = "/mcp",
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(base, json=body).json()  # type: ignore[no-any-return]


_MODULE_CACHE: dict[str, ModuleType] = {}


def load_advanced_example_module() -> ModuleType:
    """Load the advanced example module once and reuse it across tests.

    The module declares a SQLAlchemy mapped class (``TaskRecord``) against
    a shared metadata. Re-importing would re-register the table and raise
    ``InvalidRequestError``, so we cache the first import.
    """

    module_name = "docs_examples_advanced_main"
    if module_name in _MODULE_CACHE:
        return _MODULE_CACHE[module_name]

    path = Path(__file__).resolve().parents[1] / "docs/examples/advanced/main.py"
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
    finally:
        if previous_database is None:
            os.environ.pop("LITESTAR_MCP_ADVANCED_DB", None)
        else:
            os.environ["LITESTAR_MCP_ADVANCED_DB"] = previous_database

    _MODULE_CACHE[module_name] = module
    return module


def test_advanced_example_tool_uses_repository_service_di() -> None:
    """The advanced example's MCP tool should resolve its repository service via DI.

    Drives the call through the real ``POST /mcp`` entry point so the live
    Litestar request scope is available — this is what lets advanced-alchemy's
    ``SQLAlchemyPlugin`` hand out a per-request ``db_session`` and
    ``providers.create_service_dependencies`` wire up the ``TaskService``.
    """

    module = load_advanced_example_module()
    app = module.app

    list_tasks_handler = get_handler_from_app(app, "/tasks", "GET")
    resolved_deps = list_tasks_handler.resolve_dependencies()
    assert "task_service" in resolved_deps
    assert "filters" in resolved_deps
    assert "db_session" in resolved_deps  # contributed by SQLAlchemyPlugin

    list_tasks_schema = generate_schema_for_handler(list_tasks_handler)
    assert "task_service" not in list_tasks_schema["properties"]
    assert "filters" not in list_tasks_schema["properties"]

    with TestClient(app=app) as client:
        result = _rpc(client, "tools/call", {"name": "list_tasks", "arguments": {}})

    assert "error" not in result, f"unexpected error: {result.get('error')}"
    content = result["result"]["content"]
    payload = json.loads(content[0]["text"])

    # AA's ``to_schema`` returns ``OffsetPagination`` — a dataclass with
    # ``items`` / ``total`` / ``limit`` / ``offset``.
    assert set(payload) >= {"items", "total", "limit", "offset"}
    assert payload["total"] == len(module.INITIAL_TASKS)
    assert len(payload["items"]) == len(module.INITIAL_TASKS)
    assert all({"id", "title", "description", "completed"} <= item.keys() for item in payload["items"])


def test_advanced_example_api_info_resource_is_static() -> None:
    """The ``api_info`` resource should be cheap and cacheable — no DI, no DB."""

    module = load_advanced_example_module()
    app = module.app

    api_info_handler = get_handler_from_app(app, "/api/info", "GET")
    resolved_deps = api_info_handler.resolve_dependencies() or {}
    assert "task_service" not in resolved_deps  # static resource — no DB coupling

    api_info_schema = generate_schema_for_handler(api_info_handler)
    assert api_info_schema["properties"] == {}

    with TestClient(app=app) as client:
        result = _rpc(client, "resources/read", {"uri": "litestar://api_info"})

    assert "error" not in result, f"unexpected error: {result.get('error')}"
    contents = result["result"]["contents"]
    api_info = json.loads(contents[0]["text"])

    assert api_info["storage_backend"] == "sqlite"
    assert "tasks_count" not in api_info


def test_advanced_example_docs_use_real_path() -> None:
    """The docs should point at the real docs example locations.

    The canonical hands-on guide is ``docs/examples/README.md``; the
    top-level Sphinx page at ``docs/examples.rst`` is a thin wrapper
    that embeds the README via a toctree entry.
    """

    examples_readme = Path("docs/examples/README.md").read_text()
    examples_page = Path("docs/examples.rst").read_text()
    getting_started = Path("docs/getting-started.rst").read_text()

    # README carries the authoritative paths for both examples
    assert "docs/examples/basic/" in examples_readme
    assert "docs/examples/advanced/" in examples_readme
    assert "cd docs/examples/basic/" in examples_readme
    assert "cd docs/examples/advanced/" in examples_readme

    # The top-level examples.rst must link to the README subpage
    assert "examples/README" in examples_page

    # Getting-started still references the example location
    assert "docs/examples/basic/" in getting_started
