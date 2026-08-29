from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


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
    _run(
        str(base_python),
        "-c",
        'import importlib.util; import litestar_mcp; assert importlib.util.find_spec("a2a") is None',
        cwd=repository,
    )

    a2a_environment = tmp_path / "a2a"
    _run(uv, "venv", str(a2a_environment), cwd=repository)
    a2a_python = a2a_environment / "bin" / "python"
    _run(uv, "pip", "install", "--python", str(a2a_python), f"{wheel}[a2a]", cwd=repository)
    _run(
        str(a2a_python),
        "-c",
        "import importlib.util; from litestar_mcp.a2a import A2AConfig, LitestarA2A; "
        'assert importlib.util.find_spec("starlette") is None; '
        'assert importlib.util.find_spec("sse_starlette") is None',
        cwd=repository,
    )
