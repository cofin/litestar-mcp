import pytest

from litestar_mcp.agent.spec import Agent, AgentGroup, AgentMessage
from litestar_mcp.agent.tools import tool
from litestar_mcp.controllers.skill import SkillController


class DummySkill(SkillController):
    name = "dummy"
    instructions = "Grounding for dummy skill."

    @tool(description="Do something.")
    def act(self, val: str) -> str:
        return f"acted: {val}"


def test_agent_message_model() -> None:
    msg = AgentMessage(role="user", content="Hello world")
    d = msg.to_dict()
    assert d["role"] == "user"
    assert d["content"] == "Hello world"


def test_agent_aggregation() -> None:
    @tool(description="Standalone tool.")
    def echo(text: str) -> str:
        return text

    agent = Agent(
        name="worker",
        instructions="Primary instructions.",
        tools=[echo],
        skills=[DummySkill()],
    )
    tools = agent.get_all_tools()
    assert len(tools) == 2
    tool_names = {t.name for t in tools}
    assert "echo" in tool_names
    assert "act" in tool_names

    instructions = agent.get_combined_instructions()
    assert "Primary instructions." in instructions
    assert "Grounding for dummy skill." in instructions


def test_agent_group_transfer_synthesis() -> None:
    coordinator = Agent(name="coordinator", instructions="Triage incoming requests.")
    specialist_a = Agent(name="search_specialist", instructions="Perform searches.")
    specialist_b = Agent(name="code_specialist", instructions="Analyze code.")

    group = AgentGroup(
        coordinator=coordinator,
        specialists=[specialist_a, specialist_b],
    )

    coord_tools = {t.name: t for t in coordinator.tools}
    assert "transfer_to_agent" in coord_tools
    transfer_tool = coord_tools["transfer_to_agent"]
    schema = transfer_tool.to_json_schema()
    enum_values = schema["parameters"]["properties"]["target_agent"]["enum"]
    assert "search_specialist" in enum_values
    assert "code_specialist" in enum_values

    assert group.get_agent("coordinator") is coordinator
    assert group.get_agent("search_specialist") is specialist_a
    with pytest.raises(KeyError):
        group.get_agent("unknown_agent")
