"""Unit tests for standalone Agent runner and skill decorators."""

from unittest.mock import patch

from litestar.testing import TestClient

from litestar_mcp.a2a.agent import Agent, a2a_skill, skill


def test_standalone_skill_decorators() -> None:
    """Test @skill and @a2a_skill decorator metadata attachment."""

    @skill(id="calc_add", name="Addition", description="Add two numbers", tags=["math"])
    def add(a: int, b: int) -> int:
        return a + b

    @a2a_skill(id="calc_sub", name="Subtraction")
    def sub(a: int, b: int) -> int:
        return a - b

    add_meta = getattr(add, "_a2a_skill", {})
    assert add_meta is not None
    assert add_meta["id"] == "calc_add"
    assert add_meta["name"] == "Addition"
    assert add_meta["tags"] == ["math"]

    sub_meta = getattr(sub, "_a2a_skill", {})
    assert sub_meta is not None
    assert sub_meta["id"] == "calc_sub"
    assert sub_meta["name"] == "Subtraction"


def test_agent_initialization() -> None:
    """Test Agent initialization with default and custom configs."""
    agent = Agent(name="CustomAgent", description="Custom description", version="3.0.0")
    assert agent.config.name == "CustomAgent"
    assert agent.config.description == "Custom description"
    assert agent.config.version == "3.0.0"
    assert agent.registry is not None
    assert agent.plugin is not None


def test_agent_skill_registration_and_execution() -> None:
    """Test registering skills on Agent and executing via agent.app."""
    agent = Agent(name="MathAgent")

    @agent.skill(name="multiply", description="Multiply two integers")
    def multiply(x: int, y: int) -> dict[str, int]:
        return {"result": x * y}

    assert "multiply" in agent.discovered_skills

    with TestClient(app=agent.app) as client:
        card_resp = client.get("/.well-known/agent-card.json")
        assert card_resp.status_code == 200
        card = card_resp.json()
        assert card["name"] == "MathAgent"
        assert len(card["skills"]) == 1
        assert card["skills"][0]["id"] == "multiply"

        rpc_resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/send",
                "params": {
                    "skill": "multiply",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "data", "data": {"x": 7, "y": 8}}],
                    },
                },
            },
        )
        assert rpc_resp.status_code == 200
        result = rpc_resp.json()["result"]
        assert result["status"]["state"] == "completed"
        assert result["artifacts"][0]["parts"][0]["data"] == {"result": 56}


def test_agent_run_invocation() -> None:
    """Test agent.run delegates flags to the execution runner."""
    agent = Agent(name="RunAgent")

    with patch.object(agent, "_execute_cli") as mock_exec:
        agent.run(host="127.0.0.1", port=9999)
        mock_exec.assert_called_once_with(["run", "--host", "127.0.0.1", "--port", "9999"])
