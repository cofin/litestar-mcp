from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from litestar.cli._utils import LitestarEnv

from litestar_mcp import MCP
from litestar_mcp.mcp.app import _resolve_litestar_app_env

pytestmark = pytest.mark.unit

# Define global instances to test _resolve_litestar_app_env
mcp_resolve_test = MCP("resolve-test")
app_resolve_test = mcp_resolve_test.app


def test_resolve_litestar_app_env_success() -> "None":
    resolved = _resolve_litestar_app_env(app_resolve_test)
    assert resolved == "tests.unit.test_standalone_sse:app_resolve_test"


@patch("litestar_mcp.mcp.app._resolve_litestar_app_env")
@patch("litestar.cli.main.litestar_group.main")
@patch("litestar.cli._utils.LitestarEnv.from_env")
@pytest.mark.parametrize("use_default", [False, True])
def test_standalone_streamable_http_success(
    mock_from_env: "MagicMock",
    mock_main: "MagicMock",
    mock_resolve: "MagicMock",
    use_default: bool,
) -> "None":
    mock_resolve.return_value = "test_module:app"
    mock_env = MagicMock(spec=LitestarEnv)
    mock_from_env.return_value = mock_env

    mcp = MCP("test-mcp")
    if use_default:
        mcp.run(reload=True, port=8888)
    else:
        mcp.run(transport="streamable-http", reload=True, port=8888)

    mock_resolve.assert_called_once_with(mcp.app)
    mock_from_env.assert_called_once_with("test_module:app")
    mock_main.assert_called_once_with(
        args=["run", "--reload", "--port", "8888"],
        obj=mock_env,
    )


@patch("litestar_mcp.mcp.app._resolve_litestar_app_env")
def test_standalone_streamable_http_resolve_failure_raises(
    mock_resolve: "MagicMock",
) -> "None":
    mock_resolve.return_value = None

    mcp = MCP("test-mcp")
    with pytest.raises(RuntimeError, match="Could not resolve the Litestar application import path"):
        mcp.run(transport="streamable-http")


@pytest.mark.parametrize("transport", ["sse", "unknown"])
def test_standalone_rejects_unsupported_transport(transport: Any) -> None:
    with pytest.raises(ValueError, match=f"Unsupported transport: {transport}"):
        MCP("test-mcp").run(transport=transport)
