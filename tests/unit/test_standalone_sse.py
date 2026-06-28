from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from litestar.cli._utils import LitestarEnv

from litestar_mcp import MCP
from litestar_mcp.app import _resolve_litestar_app_env

pytestmark = pytest.mark.unit

# Define global instances to test _resolve_litestar_app_env
mcp_resolve_test = MCP("resolve-test")
app_resolve_test = mcp_resolve_test.app


def test_resolve_litestar_app_env_success() -> None:
    resolved = _resolve_litestar_app_env(app_resolve_test)
    assert resolved == "tests.unit.test_standalone_sse:app_resolve_test"


@patch("litestar_mcp.app._resolve_litestar_app_env")
@patch("litestar.cli.main.litestar_group.main")
@patch("litestar.cli._utils.LitestarEnv.from_env")
def test_standalone_sse_success(
    mock_from_env: MagicMock,
    mock_main: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    mock_resolve.return_value = "test_module:app"
    mock_env = MagicMock(spec=LitestarEnv)
    mock_from_env.return_value = mock_env

    mcp = MCP("test-mcp")
    mcp.run(transport="sse", reload=True, port=8888)

    mock_resolve.assert_called_once_with(mcp.app)
    mock_from_env.assert_called_once_with("test_module:app")
    mock_main.assert_called_once_with(
        args=["run", "--reload", "--port", "8888"],
        obj=mock_env,
    )


@patch("litestar_mcp.app._resolve_litestar_app_env")
def test_standalone_sse_resolve_failure_raises(
    mock_resolve: MagicMock,
) -> None:
    mock_resolve.return_value = None

    mcp = MCP("test-mcp")
    with pytest.raises(RuntimeError, match="Could not resolve the Litestar application import path"):
        mcp.run(transport="sse")
