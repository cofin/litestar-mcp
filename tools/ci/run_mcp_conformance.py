"""Run the pinned MCP 2026-07-28 server conformance scenarios."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "2026-07-28"
SCENARIOS = (
    "server-stateless",
    "completion-complete",
    "tools-list",
    "tools-call-simple-text",
    "tools-call-image",
    "tools-call-audio",
    "tools-call-embedded-resource",
    "tools-call-mixed-content",
    "tools-call-error",
    "json-schema-2020-12",
    "server-sse-multiple-streams",
    "resources-list",
    "resources-read-text",
    "resources-read-binary",
    "resources-templates-read",
    "sep-2164-resource-not-found",
    "prompts-list",
    "prompts-get-simple",
    "prompts-get-with-args",
    "prompts-get-embedded-resource",
    "prompts-get-with-image",
    "dns-rebinding-protection",
    "caching",
    "http-header-validation",
    "http-custom-header-server-validation",
    "tasks-lifecycle",
    "tasks-capability-negotiation",
    "tasks-wire-fields",
    "tasks-request-state-removal",
    "tasks-mrtr-input",
    "tasks-request-headers",
    "tasks-dispatch-and-envelope",
    "tasks-status-notifications",
    "tasks-required-task-error",
    "tasks-mrtr-composition",
    "input-required-result-basic-elicitation",
    "input-required-result-basic-sampling",
    "input-required-result-basic-list-roots",
    "input-required-result-request-state",
    "input-required-result-multiple-input-requests",
    "input-required-result-multi-round",
    "input-required-result-missing-input-response",
    "input-required-result-non-tool-request",
    "input-required-result-result-type",
    "input-required-result-unsupported-methods",
    "input-required-result-tampered-state",
    "input-required-result-capability-check",
    "input-required-result-ignore-extra-params",
    "input-required-result-validate-input",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            msg = f"conformance fixture exited with status {process.returncode}"
            raise RuntimeError(msg)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    msg = "timed out waiting for the conformance fixture"
    raise TimeoutError(msg)


def main() -> int:
    """Run every required stateless scenario without an expected-failures baseline."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    environment["MCP_CONFORMANCE_PORT"] = str(port)
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.conformance.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
    )
    try:
        _wait_for_server(port, server)
        npm = shutil.which("npm")
        if npm is None:
            msg = "npm is required to run MCP conformance"
            raise RuntimeError(msg)
        for scenario in SCENARIOS:
            completed = subprocess.run(
                [
                    npm,
                    "exec",
                    "--offline",
                    "--",
                    "conformance",
                    "server",
                    "--url",
                    url,
                    "--scenario",
                    scenario,
                    "--spec-version",
                    PROTOCOL_VERSION,
                    "--force",
                ],
                cwd=PROJECT_ROOT,
                check=False,
            )
            if completed.returncode:
                return completed.returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
