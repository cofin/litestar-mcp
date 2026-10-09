from __future__ import annotations

from typing import Any

from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.exceptions import MissingDependencyError


def agent_to_a2a(agent_or_group: Agent | AgentGroup, path: str = "/a2a") -> Any:
    """Mount an Agent or AgentGroup as an A2A-compliant Litestar plugin."""
    try:
        from a2a.types import AgentCard, AgentSkill

        from litestar_mcp.a2a import A2AConfig, LitestarA2A
    except ImportError as exc:
        raise MissingDependencyError(
            package="a2a-sdk",
            extra="a2a",
        ) from exc

    agent = agent_or_group if isinstance(agent_or_group, Agent) else agent_or_group.coordinator
    agent_name = agent.name
    agent_description = agent.description or "Autonomous Litestar Agent"

    skills_list: list[AgentSkill] = []
    for s in agent.skills:
        inst = s() if isinstance(s, type) else s
        if hasattr(inst, "to_agent_skill"):
            data = inst.to_agent_skill()
            skills_list.append(
                AgentSkill(
                    name=data["name"],
                    description=data["description"],
                    tags=data.get("tags", []),
                    examples=data.get("examples", []),
                )
            )

    card = AgentCard(
        name=agent_name,
        description=agent_description,
        skills=skills_list,
        url=path,
    )

    class BridgeRequestHandler:
        async def handle_request(self, req: Any) -> Any:
            return {"status": "success", "message": f"Turn handled by {agent_name}"}

    config = A2AConfig(path=path)
    return LitestarA2A(
        agent_card=card,
        request_handler=BridgeRequestHandler(),
        config=config,
    )
