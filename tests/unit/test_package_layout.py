"""Tests for the package layout and the lazy A2A root exports."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import litestar_mcp
from litestar_mcp.core.exceptions import MissingDependencyError


def test_root_exposes_a2a_names_lazily() -> None:
    a2a = importlib.import_module("litestar_mcp.a2a")

    assert "A2AConfig" in dir(litestar_mcp)
    assert litestar_mcp.A2AConfig is a2a.A2AConfig
    assert litestar_mcp.LitestarA2A is a2a.LitestarA2A
    assert "LitestarA2A" not in litestar_mcp.__all__


def test_root_import_does_not_import_a2a() -> None:
    script = (
        "import sys; import litestar_mcp; import litestar_mcp.mcp.bridge; "
        "assert 'a2a' not in sys.modules; assert 'litestar_mcp.a2a' not in sys.modules"
    )

    subprocess.run([sys.executable, "-c", script], check=True)  # noqa: S603


def test_missing_a2a_sdk_raises_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("litestar_mcp.a2a", "litestar_mcp.a2a.config", "litestar_mcp.a2a.plugin"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delattr(litestar_mcp, "a2a", raising=False)
    monkeypatch.setitem(sys.modules, "a2a", None)

    with pytest.raises(MissingDependencyError, match="a2a-sdk"):
        _ = litestar_mcp.LitestarA2A


def test_unknown_root_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="does_not_exist"):
        _ = litestar_mcp.does_not_exist


def test_top_level_package_holds_only_subpackages() -> None:
    package_dir = Path(litestar_mcp.__file__).parent
    modules = sorted(path.name for path in package_dir.glob("*.py"))

    assert modules == ["__init__.py", "__metadata__.py"]
