"""Public API backwards-compatibility assertions for MCP task configuration and store."""

import dataclasses
import inspect

from litestar_mcp.mcp.config import MCPTaskConfig
from litestar_mcp.mcp.tasks import MCPTaskStore


def test_mcp_task_config_field_order_backward_compatibility() -> None:
    """v0.14 fields must remain an ordered prefix of MCPTaskConfig, with executor appended."""
    expected_prefix = ["store", "default_ttl_ms", "max_ttl_ms", "poll_interval_ms", "status_callback"]
    fields = [field.name for field in dataclasses.fields(MCPTaskConfig)]
    assert fields[: len(expected_prefix)] == expected_prefix
    assert fields[len(expected_prefix) :] == ["executor"]


def test_mcp_task_store_init_parameters_compatibility() -> None:
    """MCPTaskStore.__init__ parameters must match v0.14 exactly without dead queue stubs."""
    sig = inspect.signature(MCPTaskStore.__init__)
    params = list(sig.parameters.keys())
    assert params == ["self", "store", "default_ttl_ms", "max_ttl_ms", "poll_interval_ms", "status_callback"]
