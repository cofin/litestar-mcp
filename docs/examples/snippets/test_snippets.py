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

CLIENT_ONLY_SNIPPET_MODULES = {"adk_snippets"}
CONFIG_SNIPPET_MODULES = {"configuration_stateless"}
HANDLER_ONLY_SNIPPET_MODULES = {"tool_explicit_input_schema", "tool_task_input_before_start"}
NON_APP_SNIPPET_MODULES = CLIENT_ONLY_SNIPPET_MODULES | CONFIG_SNIPPET_MODULES | HANDLER_ONLY_SNIPPET_MODULES

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


@pytest.mark.parametrize("module_name", sorted(CLIENT_ONLY_SNIPPET_MODULES | HANDLER_ONLY_SNIPPET_MODULES))
def test_non_app_snippet_imports(module_name: "str") -> "None":
    """Import client-only and handler-only snippets that do not define a Litestar application."""
    importlib.import_module(f"docs.examples.snippets.{module_name}")


@pytest.mark.parametrize("module_name", sorted(CONFIG_SNIPPET_MODULES))
def test_config_snippet_exposes_mcp_config(module_name: "str") -> "None":
    """Import configuration snippets and verify they define a module-level ``MCPConfig``."""
    mod = importlib.import_module(f"docs.examples.snippets.{module_name}")
    assert isinstance(mod.config, MCPConfig)
