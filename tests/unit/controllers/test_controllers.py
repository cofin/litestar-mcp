from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp import (
    LitestarMCP,
    PromptController,
    PromptMessage,
    SkillController,
    prompt,
    tool,
)


class SamplePromptController(PromptController):
    prefix = "test_prompt"
    instructions = "Grounding instructions for test prompts."

    @prompt(title="Basic Prompt", description="Returns basic greeting.")
    def greet(self, name: str) -> str:
        return f"Hello, {name}!"

    @prompt(title="Structured Prompt", description="Returns structured messages.")
    def structured(self, topic: str) -> list[PromptMessage]:
        return [
            PromptMessage.user(f"Explain {topic}"),
            PromptMessage.assistant("Certainly!"),
        ]


class SampleSkillController(SkillController):
    name = "math_ops"
    description = "Basic arithmetic operations"
    prefix = "math"
    instructions = "Always check for division by zero."

    @tool(description="Add two numbers.")
    def add(self, a: int, b: int) -> int:
        return a + b

    @prompt(title="Math Problem", description="Prompt to solve a math problem.")
    def solve(self, problem: str) -> str:
        return f"Solve this problem: {problem}"


def test_prompt_controller_formatting() -> None:
    ctrl = SamplePromptController()
    res = ctrl.format_prompt_response("Simple string output")
    assert len(res) == 2
    assert res[0]["role"] == "system"
    assert res[0]["content"]["text"] == "Grounding instructions for test prompts."
    assert res[1]["role"] == "user"
    assert res[1]["content"]["text"] == "Simple string output"

    msg_res = ctrl.format_prompt_response(PromptMessage.assistant("Assistant reply"))
    assert len(msg_res) == 2
    assert msg_res[1]["role"] == "assistant"

    dict_res = ctrl.format_prompt_response({"role": "user", "content": "A dict question", "name": "user1"})
    assert len(dict_res) == 2
    assert dict_res[1]["name"] == "user1"

    list_res = ctrl.format_prompt_response(
        [
            PromptMessage.system("Existing system message"),
            {"role": "user", "content": "Follow-up"},
            "Raw string in list",
        ]
    )
    assert len(list_res) == 3
    assert list_res[0]["role"] == "system"

    int_res = ctrl.format_prompt_response(999)
    assert len(int_res) == 2
    assert int_res[1]["content"]["text"] == "999"


def test_prompt_controller_registrations() -> None:
    ctrl = SamplePromptController()
    registrations = ctrl.get_prompt_registrations()
    assert len(registrations) == 2
    reg_names = {r.name for r in registrations}
    assert "test_prompt/greet" in reg_names
    assert "test_prompt/structured" in reg_names

    greet_reg = next(r for r in registrations if r.name == "test_prompt/greet")
    assert greet_reg.fn is not None
    output = greet_reg.fn(name="Alice")
    assert len(output) == 2
    assert output[1]["content"]["text"] == "Hello, Alice!"


def test_skill_controller_capabilities() -> None:
    skill = SampleSkillController()
    assert skill.get_instructions() == "Always check for division by zero."

    tools = skill.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "add"
    assert tools[0].fn(skill, 2, 3) == 5

    prompts = skill.get_prompts()
    assert len(prompts) == 1
    assert prompts[0].name == "math/solve"

    agent_skill = skill.to_agent_skill()
    assert agent_skill["name"] == "math_ops"
    assert agent_skill["description"] == "Basic arithmetic operations"


def test_plugin_registration_with_controllers() -> None:
    mcp_plugin = LitestarMCP(
        controllers=[SamplePromptController],
        skill_controllers=[SampleSkillController],
    )
    app = Litestar(
        route_handlers=[SamplePromptController, SampleSkillController],
        plugins=[mcp_plugin],
    )
    with TestClient(app=app) as client:
        assert client is not None
        assert "test_prompt/greet" in mcp_plugin.discovered_prompts
        assert "math/solve" in mcp_plugin.discovered_prompts
