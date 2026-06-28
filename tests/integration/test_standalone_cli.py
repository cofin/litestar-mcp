from __future__ import annotations

import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_mcp_run_cli_execution(tmp_path: Path) -> None:
    # 1. Write the test app file
    app_file = tmp_path / "cli_test_app.py"
    app_file.write_text(
        textwrap.dedent(
            """
        import asyncio
        import os
        import signal
        import sys
        from litestar_mcp.app import MCP

        async def stop_server(app) -> None:
            async def _fn() -> None:
                await asyncio.sleep(2)
                print("STOPPING_SERVER_NOW", flush=True)
                os.kill(os.getpid(), signal.SIGTERM)
            asyncio.create_task(_fn())

        mcp = MCP("cli-test", on_startup=[stop_server])
        app = mcp.app

        if __name__ == "__main__":
            mcp.run(port=23456)
        """
        )
    )

    # 2. Run the subprocess with sys.executable to preserve environment
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(app_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(f"Server did not stop within timeout. stdout: {stdout}, stderr: {stderr}")

    # 3. Assertions
    # Process should exit via SIGTERM (returns -15 on Unix)
    assert process.returncode in (0, -15)
    assert "STOPPING_SERVER_NOW" in stdout
    # Litestar CLI outputs starting logs to stderr
    assert "Started server process" in stderr or "Started server process" in stdout
