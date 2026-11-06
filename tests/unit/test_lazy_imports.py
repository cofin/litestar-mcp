"""Unit tests verifying lazy imports and clean package boundary."""

import sys

import pytest


def test_root_import_does_not_load_agent_runtime_or_heavy_dependencies() -> None:
    """Importing litestar_mcp does not import google.genai, litestar_queues, or agent runtime."""
    saved_modules = {
        mod: sys.modules[mod]
        for mod in list(sys.modules.keys())
        if mod.startswith(("google.genai", "litestar_queues", "litestar_mcp.agent.runtime"))
    }
    for mod in saved_modules:
        sys.modules.pop(mod, None)

    try:
        import litestar_mcp

        assert hasattr(litestar_mcp, "PromptController")
        assert hasattr(litestar_mcp, "SkillController")
        assert hasattr(litestar_mcp, "tool")
        assert hasattr(litestar_mcp, "prompt")

        assert "google.genai" not in sys.modules
        assert "litestar_queues" not in sys.modules
        assert "litestar_mcp.agent.runtime" not in sys.modules
    finally:
        sys.modules.update(saved_modules)


def test_agent_lazy_attribute_resolution() -> None:
    """Accessing attributes on litestar_mcp.agent resolves lazily."""
    from litestar_mcp import agent

    agent_cls = agent.Agent
    assert agent_cls is not None
    assert agent_cls.__name__ == "Agent"

    workflow_cls = agent.DynamicWorkflow
    assert workflow_cls is not None


@pytest.mark.parametrize(
    "symbol",
    [
        "ANONYMOUS_OWNER",
        "SessionStore",
        "MemorySessionStore",
        "LitestarStoreSessionStore",
        "ChatRequest",
        "TurnResult",
        "SessionHistory",
        "SessionNotFoundError",
    ],
)
def test_agent_public_reexports(symbol: str) -> None:
    """Public session and controller symbols are exported on litestar_mcp.agent."""
    import litestar_mcp.agent as agent_pkg

    assert symbol in agent_pkg.__all__
    assert symbol in dir(agent_pkg)
    assert getattr(agent_pkg, symbol) is not None


@pytest.mark.parametrize(
    "symbol",
    [
        "Tool",
        "ToolCall",
        "ToolResult",
        "tool",
        "ToolContext",
        "TelemetryConfig",
        "SpanManager",
        "encode_json",
        "decode_json",
        "to_json",
        "from_json",
    ],
)
def test_core_public_reexports(symbol: str) -> None:
    """Protocol-agnostic primitives and serialization helpers are exported on litestar_mcp.core."""
    import litestar_mcp.core as core_pkg

    assert symbol in core_pkg.__all__
    assert symbol in dir(core_pkg)
    assert getattr(core_pkg, symbol) is not None


def test_core_serialization_exports_and_unknown_attribute() -> None:
    """Core serialization exports encode_json/decode_json and core raises AttributeError on unknown names."""
    import litestar_mcp.core as core_pkg
    from litestar_mcp.core import serialization

    assert "encode_json" in serialization.__all__
    assert "decode_json" in serialization.__all__

    missing_attr = "NonExistent"
    with pytest.raises(AttributeError, match=r"module 'litestar_mcp\.core' has no attribute 'NonExistent'"):
        getattr(core_pkg, missing_attr)
