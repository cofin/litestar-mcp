import shutil
import subprocess
from pathlib import Path

import pytest

_COMMON_PROBE = """import importlib.util
from importlib.metadata import distributions
from pathlib import Path
import sys

import litestar_mcp
import litestar_mcp.mcp.bridge

assert Path(litestar_mcp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
for name in ("starlette", "sse_starlette", "fastapi", "uvicorn"):
    assert importlib.util.find_spec(name) is None, name
    assert not any(module == name or module.startswith(name + ".") for module in sys.modules), name
installed = {distribution.metadata["Name"].lower().replace("_", "-") for distribution in distributions()}
assert not installed.intersection({"starlette", "sse-starlette", "fastapi", "uvicorn"}), installed
"""

_BASE_PROBE = """
from litestar_mcp.core.exceptions import MissingDependencyError

assert importlib.util.find_spec("a2a") is None
assert "a2a" not in sys.modules
assert "litestar_mcp.a2a" not in sys.modules
try:
    litestar_mcp.LitestarA2A
except MissingDependencyError:
    pass
else:
    raise SystemExit("expected MissingDependencyError without the a2a extra")
"""


def _run(*command: str, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)  # noqa: S603


def _find_uv() -> str:
    executable = shutil.which("uv")
    if executable is None:
        msg = "uv is required for the isolated wheel smoke test"
        raise RuntimeError(msg)
    return executable


@pytest.mark.integration
def test_clean_wheel_base_and_a2a_extra(tmp_path: Path) -> None:
    uv = _find_uv()

    repository = Path(__file__).parents[2]
    wheel_dir = tmp_path / "wheel"
    _run(uv, "build", "--wheel", "--out-dir", str(wheel_dir), cwd=repository)
    wheel = next(wheel_dir.glob("litestar_mcp-*.whl"))

    base_environment = tmp_path / "base"
    _run(uv, "venv", str(base_environment), cwd=repository)
    base_python = base_environment / "bin" / "python"
    _run(uv, "pip", "install", "--python", str(base_python), str(wheel), cwd=repository)
    probe = tmp_path / "probe_base.py"
    probe.write_text(_COMMON_PROBE + _BASE_PROBE)
    _run(str(base_python), "-I", str(probe), cwd=tmp_path)

    a2a_environment = tmp_path / "a2a"
    _run(uv, "venv", str(a2a_environment), cwd=repository)
    a2a_python = a2a_environment / "bin" / "python"
    _run(uv, "pip", "install", "--python", str(a2a_python), f"{wheel}[a2a]", cwd=repository)
    snippet = repository / "docs" / "examples" / "snippets" / "a2a_plugin.py"
    a2a_probe = tmp_path / "probe_a2a.py"
    a2a_probe.write_text(snippet.read_text() + "\napp = build()\nassert isinstance(app, Litestar)\n" + _COMMON_PROBE)
    _run(str(a2a_python), "-I", str(a2a_probe), cwd=tmp_path)
