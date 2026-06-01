"""Smoke test every snippet module.

Each snippet exposes ``build() -> Litestar`` or a module-level ``app`` variable;
importing and invoking it catches copy-paste typos and missing imports.
"""

import importlib
import pkgutil

import pytest
from litestar import Litestar

import docs.examples.snippets as snippets_pkg

SNIPPET_MODULES = [
    name
    for _finder, name, _ispkg in pkgutil.iter_modules(snippets_pkg.__path__)
    if not name.startswith("test_") and name != "jwks_cache_shared"
]


@pytest.mark.parametrize("module_name", SNIPPET_MODULES)
def test_snippet_build_returns_litestar(module_name: str) -> None:
    """Import the snippet module and verify it defines or builds a Litestar application."""
    mod = importlib.import_module(f"docs.examples.snippets.{module_name}")
    if hasattr(mod, "build"):
        app = mod.build()
    elif hasattr(mod, "app"):
        app = mod.app
    else:
        pytest.fail(f"{module_name} has neither build() nor app variable")
    assert isinstance(app, Litestar)
