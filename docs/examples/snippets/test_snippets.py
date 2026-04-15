"""Smoke test every snippet module.

Each snippet exposes ``build() -> Litestar``; importing and invoking it
catches copy-paste typos and missing imports inside the marked regions.
"""

import importlib
import pkgutil

import pytest
from litestar import Litestar

import docs.examples.snippets as snippets_pkg

SNIPPET_MODULES = [
    name for _finder, name, _ispkg in pkgutil.iter_modules(snippets_pkg.__path__) if not name.startswith("test_")
]


@pytest.mark.parametrize("module_name", SNIPPET_MODULES)
def test_snippet_build_returns_litestar(module_name: str) -> None:
    mod = importlib.import_module(f"docs.examples.snippets.{module_name}")
    assert hasattr(mod, "build"), f"{module_name} is missing the build() entrypoint"
    app = mod.build()
    assert isinstance(app, Litestar)
