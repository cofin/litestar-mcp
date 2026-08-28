"""Tests for the CLI module."""

import asyncio
import builtins
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click import Context, Group
from click.testing import CliRunner
from litestar import Litestar
from litestar.cli._utils import LitestarEnv

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.cli import mcp_group


@pytest.fixture(scope="session")
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(scope="session")
def make_env() -> Callable[..., LitestarEnv]:
    def factory(
        *,
        base_path: str = "/mcp",
        host: str | None = None,
        port: int | None = None,
    ) -> LitestarEnv:
        app = Litestar(plugins=[LitestarMCP(MCPConfig(base_path=base_path))])
        return LitestarEnv(app_path="tests.unit.test_cli:app", app=app, cwd=Path.cwd(), host=host, port=port)

    return factory


@pytest.fixture
def captured_bridge(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run_bridge(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("litestar_mcp.bridge.run_bridge", fake_run_bridge)
    return captured


def test_mcp_group_help_command(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(mcp_group, ["--help"], obj=Mock(app=None))

    assert result.exit_code == 0
    assert "Manage MCP tools and resources" in result.output
    assert "list-tools" in result.output
    assert "list-resources" in result.output
    assert "run" in result.output
    assert "bridge" in result.output


def test_litestar_mcp_registers_mcp_group() -> None:
    plugin = LitestarMCP()
    cli = Group()

    plugin.on_cli_init(cli)

    with Context(cli) as ctx:
        assert cli.get_command(ctx, "mcp") is mcp_group


@pytest.mark.parametrize(
    ("args", "env_kwargs", "expected_endpoint"),
    (
        pytest.param((), {"base_path": "/api/mcp"}, "http://127.0.0.1:8000/api/mcp", id="configured-path"),
        pytest.param(
            (),
            {"base_path": "/api/mcp", "host": "localhost", "port": 9001},
            "http://localhost:9001/api/mcp",
            id="litestar-host-port",
        ),
        pytest.param(
            ("--base-url", "https://example.test/root/"),
            {"base_path": "/api/mcp"},
            "https://example.test/root/api/mcp",
            id="base-url-preserves-path",
        ),
        pytest.param(
            (
                "--base-url",
                "https://example.test/root/",
                "--endpoint",
                "https://remote.test/custom/mcp",
            ),
            {"base_path": "/api/mcp"},
            "https://remote.test/custom/mcp",
            id="endpoint-override",
        ),
    ),
)
def test_mcp_bridge_resolves_endpoint(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
    captured_bridge: dict[str, Any],
    args: tuple[str, ...],
    env_kwargs: dict[str, Any],
    expected_endpoint: str,
) -> None:
    result = cli_runner.invoke(mcp_group, ["bridge", *args], obj=make_env(**env_kwargs))

    assert result.exit_code == 0
    assert captured_bridge["endpoint"] == expected_endpoint


def test_mcp_bridge_parses_headers_bearer_and_limits(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
    captured_bridge: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "whole-token")

    result = cli_runner.invoke(
        mcp_group,
        [
            "bridge",
            "--endpoint",
            "https://example.test/api/mcp",
            "--header",
            "X-Trace: abc",
            "--bearer-env",
            "MCP_TOKEN",
            "--header-name",
            "X-Goog-IAP-JWT-Assertion",
            "--token-prefix",
            "",
            "--timeout",
            "12",
            "--sse-read-timeout",
            "45",
            "--max-message-size",
            "-1",
        ],
        obj=make_env(),
    )

    assert result.exit_code == 0
    assert captured_bridge["endpoint"] == "https://example.test/api/mcp"
    assert captured_bridge["headers"] == {"X-Trace": "abc"}
    assert captured_bridge["header_name"] == "X-Goog-IAP-JWT-Assertion"
    assert captured_bridge["token_prefix"] == ""
    assert captured_bridge["timeout"] == 12
    assert captured_bridge["sse_read_timeout"] == 45
    assert captured_bridge["max_message_size"] == -1
    assert captured_bridge["token_provider"]() == "whole-token"


def test_mcp_bridge_rejects_multiple_bearer_sources(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
) -> None:
    result = cli_runner.invoke(
        mcp_group,
        [
            "bridge",
            "--bearer-env",
            "MCP_TOKEN",
            "--bearer-cmd",
            "dma auth token",
        ],
        obj=make_env(),
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_mcp_bridge_discover_option_is_removed(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
) -> None:
    invalid_result = cli_runner.invoke(
        mcp_group,
        [
            "bridge",
            "--base-url",
            "https://example.test/something",
            "--discover",
        ],
        obj=make_env(),
    )
    help_result = cli_runner.invoke(mcp_group, ["bridge", "--help"], obj=make_env())

    assert invalid_result.exit_code == 2
    assert help_result.exit_code == 0
    assert "--discover" not in help_result.output


def test_mcp_bridge_missing_bridge_extra_is_clean_click_error(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx_sse":
            msg = "No module named 'httpx_sse'"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = cli_runner.invoke(mcp_group, ["bridge"], obj=make_env())

    assert result.exit_code == 1
    assert "litestar-mcp[bridge]" in result.output
    assert "Traceback" not in result.output


def test_mcp_bridge_redirects_runtime_stdout_pollution(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_bridge(**kwargs: Any) -> int:
        sys.stdout.write("accidental app output\n")
        asyncio.run(kwargs["stdout"].send(b'{"jsonrpc":"2.0","id":1,"result":{}}\n'))
        return 0

    monkeypatch.setattr("litestar_mcp.bridge.run_bridge", fake_run_bridge)

    result = cli_runner.invoke(mcp_group, ["bridge"], obj=make_env())

    assert result.exit_code == 0
    assert result.stdout == '{"jsonrpc":"2.0","id":1,"result":{}}\n'
    assert "accidental app output" in result.stderr
