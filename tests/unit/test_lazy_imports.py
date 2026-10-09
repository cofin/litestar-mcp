"""Unit tests verifying lazy imports and clean package boundary."""

import sys


def test_root_import_does_not_load_agent_runtime_or_heavy_dependencies() -> None:
    """Importing litestar_mcp does not import google.genai, litestar_queues, or agent runtime."""
    for mod in list(sys.modules.keys()):
        if mod.startswith(("google.genai", "litestar_queues", "litestar_mcp.agent.runtime")):
            sys.modules.pop(mod, None)

    import litestar_mcp

    assert hasattr(litestar_mcp, "PromptController")
    assert hasattr(litestar_mcp, "SkillController")
    assert hasattr(litestar_mcp, "tool")
    assert hasattr(litestar_mcp, "prompt")

    assert "google.genai" not in sys.modules
    assert "litestar_queues" not in sys.modules
    assert "litestar_mcp.agent.runtime" not in sys.modules


def test_agent_lazy_attribute_resolution() -> None:
    """Accessing attributes on litestar_mcp.agent resolves lazily."""
    from litestar_mcp import agent

    agent_cls = agent.Agent
    assert agent_cls is not None
    assert agent_cls.__name__ == "Agent"

    workflow_cls = agent.DynamicWorkflow
    assert workflow_cls is not None
