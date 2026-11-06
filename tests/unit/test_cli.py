"""Tests for the CLI module."""

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click import Context, Group
from click.testing import CliRunner
from litestar import Litestar, get
from litestar.cli._utils import LitestarEnv

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.core.serialization import from_json
from litestar_mcp.mcp.cli import ToolExecutor, list_resources, list_tools, mcp_group
from litestar_mcp.utils import validate_tool_descriptions


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

    monkeypatch.setattr("litestar_mcp.mcp.bridge.run_bridge", fake_run_bridge)
    return captured


@pytest.fixture
def captured_stdio(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run_stdio(app: Litestar, **kwargs: Any) -> int:
        captured["app"] = app
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("litestar_mcp.mcp.stdio.run_stdio", fake_run_stdio)
    return captured


def test_mcp_group_help_command(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(mcp_group, ["--help"], obj=Mock(app=None))

    assert result.exit_code == 0
    assert "Manage MCP tools and resources" in result.output
    assert "list-tools" in result.output
    assert "list-resources" in result.output
    assert "export-tool-spec" in result.output
    assert "run" in result.output
    assert "bridge" in result.output
    assert "stdio" in result.output


def test_mcp_stdio_passes_app_and_options(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
    captured_stdio: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "whole-token")
    env = make_env()
    result = cli_runner.invoke(
        mcp_group,
        [
            "stdio",
            "--header",
            "X-Trace: abc",
            "--bearer-env",
            "MCP_TOKEN",
            "--header-name",
            "X-Goog-IAP-JWT-Assertion",
            "--token-prefix",
            "",
            "--sse-read-timeout",
            "45",
            "--max-message-size",
            "-1",
        ],
        obj=env,
    )

    assert result.exit_code == 0, result.output
    assert captured_stdio["app"] is env.app
    assert captured_stdio["headers"] == {"X-Trace": "abc"}
    assert captured_stdio["header_name"] == "X-Goog-IAP-JWT-Assertion"
    assert captured_stdio["token_prefix"] == ""
    assert captured_stdio["sse_read_timeout"] == 45
    assert captured_stdio["max_message_size"] == -1
    assert captured_stdio["token_provider"]() == "whole-token"


@pytest.mark.parametrize("option", ["--endpoint", "--base-url", "--timeout"])
def test_mcp_stdio_has_no_remote_options(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
    captured_stdio: dict[str, Any],
    option: str,
) -> None:
    result = cli_runner.invoke(mcp_group, ["stdio", option, "x"], obj=make_env())

    assert result.exit_code == 2
    assert "No such option" in result.output


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


def test_mcp_bridge_redirects_runtime_stdout_pollution(
    cli_runner: CliRunner,
    make_env: Callable[..., LitestarEnv],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_bridge(**kwargs: Any) -> int:
        sys.stdout.write("accidental app output\n")
        asyncio.run(kwargs["stdout"].send(b'{"jsonrpc":"2.0","id":1,"result":{}}\n'))
        return 0

    monkeypatch.setattr("litestar_mcp.mcp.bridge.run_bridge", fake_run_bridge)

    result = cli_runner.invoke(mcp_group, ["bridge"], obj=make_env())

    assert result.exit_code == 0
    assert result.stdout == '{"jsonrpc":"2.0","id":1,"result":{}}\n'
    assert "accidental app output" in result.stderr


def test_mcp_export_tool_spec_stdout_file_and_strict_bounds(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    @get("/items", description="List catalog items.", opt={"mcp_tool": "list_items"}, sync_to_thread=False)
    def list_items(limit: int = 10) -> list[str]:
        """List catalog items.

        Args:
            limit: Maximum number of items to return.
        """
        return []

    app = Litestar(route_handlers=[list_items], plugins=[LitestarMCP(MCPConfig())])
    env = LitestarEnv(app_path="tests.unit.test_cli:app", app=app, cwd=Path.cwd(), host=None, port=None)

    stdout_result = cli_runner.invoke(mcp_group, ["export-tool-spec"], obj=env)
    assert stdout_result.exit_code == 0, stdout_result.output
    payload = from_json(stdout_result.stdout)
    assert len(payload["tools"]) == 1
    tool_entry = payload["tools"][0]
    assert tool_entry["name"] == "list_items"
    assert tool_entry["description"] == "List catalog items."
    assert tool_entry["inputSchema"]["properties"]["limit"]["description"] == "Maximum number of items to return."
    assert tool_entry["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    }

    output_file = tmp_path / "tools.json"
    file_result = cli_runner.invoke(
        mcp_group,
        ["export-tool-spec", "--output", str(output_file), "--strict-bounds"],
        obj=env,
    )
    assert file_result.exit_code == 0, file_result.output
    file_payload = from_json(output_file.read_text(encoding="utf-8"))
    assert file_payload == payload

    @get("/verbose", description="v" * 1050, opt={"mcp_tool": "verbose_tool"}, sync_to_thread=False)
    def verbose_tool() -> dict[str, str]:
        return {"ok": "yes"}

    bad_app = Litestar(route_handlers=[verbose_tool], plugins=[LitestarMCP(MCPConfig())])
    bad_env = LitestarEnv(app_path="tests.unit.test_cli:bad_app", app=bad_app, cwd=Path.cwd(), host=None, port=None)
    strict_fail = cli_runner.invoke(mcp_group, ["export-tool-spec", "--strict-bounds"], obj=bad_env)
    assert strict_fail.exit_code != 0
    assert "verbose_tool" in strict_fail.output


def test_validate_tool_descriptions_traverses_defs_nested_items_combinators_and_cycles() -> None:
    """validate_tool_descriptions catches violations in $defs, nested properties, items, and anyOf without cycling."""
    long_param = "p" * 751
    long_output = "o" * 301
    tools = [
        {
            "name": "complex_tool",
            "description": "Valid tool description.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "payload": {"$ref": "#/$defs/InputNode"},
                    "dangling": {"$ref": "#/$defs/MissingNode"},
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": long_param},
                            },
                        },
                    },
                    "tuple_items": {
                        "type": "array",
                        "prefixItems": [
                            {
                                "type": "object",
                                "properties": {
                                    "prefix_field": {"type": "string", "description": long_param},
                                },
                            }
                        ],
                    },
                    "extra_map": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "properties": {
                                "extra_field": {"type": "string", "description": long_param},
                            },
                        },
                    },
                    "choice": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "branch_field": {"type": "string", "description": long_param},
                                },
                            },
                            {"type": "null"},
                        ]
                    },
                },
                "$defs": {
                    "InputNode": {
                        "type": "object",
                        "properties": {
                            "nested_text": {"type": "string", "description": long_param},
                            "parent": {"$ref": "#/$defs/InputNode"},
                        },
                    },
                    "UnreferencedNode": {
                        "type": "object",
                        "properties": {
                            "orphan_field": {"type": "string", "description": long_param},
                        },
                    },
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "result": {"$ref": "#/$defs/OutputNode"},
                },
                "$defs": {
                    "OutputNode": {
                        "type": "object",
                        "properties": {
                            "detail": {"type": "string", "description": long_output},
                        },
                    }
                },
            },
        }
    ]
    violations = validate_tool_descriptions(tools)
    assert (
        "Tool 'complex_tool' parameter 'payload.nested_text' description length 751 exceeds maximum 750" in violations
    )
    assert "Tool 'complex_tool' parameter 'tags.label' description length 751 exceeds maximum 750" in violations
    assert (
        "Tool 'complex_tool' parameter 'tuple_items.prefix_field' description length 751 exceeds maximum 750"
        in violations
    )
    assert (
        "Tool 'complex_tool' parameter 'extra_map.extra_field' description length 751 exceeds maximum 750" in violations
    )
    assert (
        "Tool 'complex_tool' parameter 'choice.branch_field' description length 751 exceeds maximum 750" in violations
    )
    assert (
        "Tool 'complex_tool' parameter 'UnreferencedNode.orphan_field' description length 751 exceeds maximum 750"
        in violations
    )
    assert "Tool 'complex_tool' output field 'result.detail' description length 301 exceeds maximum 300" in violations


def test_cli_list_and_run_respect_should_include_handler_filters(cli_runner: CliRunner) -> None:
    """list-tools, list-resources, and ToolExecutor filter handlers via should_include_handler."""

    @get("/public-tool", mcp_tool="public_tool", tags=["public"], sync_to_thread=False)
    def public_tool() -> dict[str, str]:
        return {"ok": "public"}

    @get("/hidden-tool", mcp_tool="hidden_tool", tags=["internal"], sync_to_thread=False)
    def hidden_tool() -> dict[str, str]:
        return {"ok": "hidden"}

    @get("/public-res", mcp_resource="public_res", tags=["public"], sync_to_thread=False)
    def public_res() -> dict[str, str]:
        return {"res": "public"}

    @get("/excluded-res", mcp_resource="excluded_res", tags=["public"], sync_to_thread=False)
    def excluded_res() -> dict[str, str]:
        return {"res": "excluded"}

    config = MCPConfig(
        exclude_tags=["internal"],
        exclude_operations=["excluded_res"],
    )
    app = Litestar(
        route_handlers=[public_tool, hidden_tool, public_res, excluded_res],
        plugins=[LitestarMCP(config=config)],
    )
    env = LitestarEnv(app_path="tests.unit.test_cli:app", app=app, cwd=Path.cwd(), host=None, port=None)
    ctx = Context(mcp_group, obj={"app": app})
    executor = ToolExecutor(name="run")

    assert executor.list_commands(ctx) == ["public_res", "public_tool"]
    assert executor.get_command(ctx, "hidden_tool") is None
    assert executor.get_command(ctx, "excluded_res") is None
    assert executor.get_command(ctx, "public_tool") is not None

    tools_out = cli_runner.invoke(mcp_group, ["list-tools"], obj=env)
    assert tools_out.exit_code == 0
    assert "public_tool" in tools_out.output
    assert "hidden_tool" not in tools_out.output

    res_out = cli_runner.invoke(mcp_group, ["list-resources"], obj=env)
    assert res_out.exit_code == 0
    assert "public_res" in res_out.output
    assert "excluded_res" not in res_out.output

    assert list_tools.help == "List all available MCP tools."
    assert list_resources.help == "List all available MCP resources."
