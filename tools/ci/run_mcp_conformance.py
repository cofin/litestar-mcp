"""Run the pinned MCP 2026-07-28 server conformance scenarios.

Every pinned scenario runs and is reported. The 0.2.0-alpha.11 ``wire-schema-valid`` check rejects
conformant ``resultType: "task"`` results because its union validator demands ``CallToolResult.content``;
that check is waived on the task scenarios listed below only, and ``MCP_CONFORMANCE_STRICT=1`` disables
the waiver.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
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
KNOWN_VALIDATOR_DEFECT_CHECK = "wire-schema-valid"
KNOWN_VALIDATOR_DEFECT_SCENARIOS = frozenset(
    {
        "tasks-lifecycle",
        "tasks-capability-negotiation",
        "tasks-wire-fields",
        "tasks-request-state-removal",
        "tasks-mrtr-input",
        "tasks-request-headers",
        "tasks-dispatch-and-envelope",
        "tasks-mrtr-composition",
    }
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
    """Run every pinned scenario, report each result, and waive only the documented validator defect."""
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
        strict = os.environ.get("MCP_CONFORMANCE_STRICT") == "1"
        results: list[tuple[str, str, list[str]]] = []
        with tempfile.TemporaryDirectory(prefix="mcp-conformance-") as tmp:
            output_root = Path(tmp)
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
                        "--output-dir",
                        str(output_root / scenario),
                    ],
                    cwd=PROJECT_ROOT,
                    check=False,
                )
                checks_file = next((output_root / scenario).rglob("checks.json"), None)
                if checks_file is None:
                    results.append((scenario, "failed", ["no checks.json"]))
                    continue
                checks = json.loads(checks_file.read_text())
                failures = [record["id"] for record in checks if record["status"] == "FAILURE"]
                if (
                    failures == [KNOWN_VALIDATOR_DEFECT_CHECK]
                    and scenario in KNOWN_VALIDATOR_DEFECT_SCENARIOS
                    and not strict
                ):
                    status = "waived"
                elif not failures and completed.returncode == 0:
                    status = "passed"
                else:
                    status = "failed"
                results.append((scenario, status, failures))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    for scenario, status, failures in results:
        sys.stdout.write(f"{status:<7} {scenario} {' '.join(failures)}\n")
    counts = {name: sum(1 for _, status, _ in results if status == name) for name in ("passed", "waived", "failed")}
    sys.stdout.write(f"passed={counts['passed']} waived={counts['waived']} failed={counts['failed']}\n")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
