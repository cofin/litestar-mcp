"""A2A 1.0 protocol bridge for Agent and AgentGroup specifications."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from litestar_mcp.agent.runtime import TurnRequest
from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.exceptions import MissingDependencyError

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.events import EventQueue

    from litestar_mcp.a2a import LitestarA2A
    from litestar_mcp.agent.runtime import AgentRuntime


def agent_to_a2a(
    agent_or_group: Agent | AgentGroup,
    path: str = "/a2a",
    *,
    base_url: str = "http://localhost:8000",
    runtime: AgentRuntime | None = None,
) -> LitestarA2A:
    """Mount an Agent or AgentGroup as an A2A 1.0 Litestar plugin.

    Args:
        agent_or_group: Single agent or multi-agent group to expose over A2A.
        path: Route path or absolute URL for the A2A JSON-RPC endpoint.
        base_url: Base URL used to construct the advertised ``AgentInterface``
            when ``path`` is a relative route path.
        runtime: Optional ``AgentRuntime`` instance used to execute incoming
            A2A ``SendMessage`` and ``SendStreamingMessage`` requests.

    Returns:
        Configured ``LitestarA2A`` plugin instance.
    """
    try:
        from a2a.server.agent_execution import AgentExecutor
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.tasks import InMemoryTaskStore
        from a2a.types import (
            AgentCapabilities,
            AgentCard,
            AgentInterface,
            AgentSkill,
            Artifact,
            Part,
            Task,
            TaskArtifactUpdateEvent,
            TaskState,
            TaskStatus,
            TaskStatusUpdateEvent,
        )

        from litestar_mcp.a2a import A2AConfig, LitestarA2A
    except ImportError as exc:
        raise MissingDependencyError(
            package="a2a-sdk",
            extra="a2a",
        ) from exc

    agent = agent_or_group if isinstance(agent_or_group, Agent) else agent_or_group.coordinator
    agent_name = agent.name
    agent_description = agent.description or "Autonomous Litestar Agent"

    if path.startswith(("http://", "https://")):
        interface_url = path
        route_path = urlparse(path).path or "/a2a"
    else:
        route_path = path if path.startswith("/") else f"/{path}"
        interface_url = f"{base_url.rstrip('/')}{route_path}"

    skills_list: list[AgentSkill] = []
    for s in agent.skills:
        inst = s() if isinstance(s, type) else s
        if hasattr(inst, "to_agent_skill"):
            data: dict[str, Any] = inst.to_agent_skill()
            skills_list.append(
                AgentSkill(
                    id=str(data.get("id", data["name"])),
                    name=str(data["name"]),
                    description=str(data["description"]),
                    tags=list(data.get("tags", [])),
                    examples=list(data.get("examples", [])),
                )
            )

    card = AgentCard(
        name=agent_name,
        description=agent_description,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=skills_list,
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                url=interface_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
    )

    class _BridgeAgentExecutor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            if context.current_task is None:
                await event_queue.enqueue_event(
                    Task(
                        id=context.task_id or "",
                        context_id=context.context_id or "",
                        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                        history=[context.message] if context.message is not None else [],
                    )
                )
            user_input = context.get_user_input()
            if runtime is not None:
                turn_res = await runtime.run_turn(
                    TurnRequest(
                        user_message=user_input,
                        session_id=context.context_id or "a2a-default",
                        turn_id=context.task_id or "a2a-turn",
                    )
                )
                answer_text = turn_res.content
            else:
                answer_text = f"Handled by {agent_name}: {user_input}"

            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=context.task_id or "",
                    context_id=context.context_id or "",
                    artifact=Artifact(artifact_id="response", parts=[Part(text=answer_text)]),
                    last_chunk=True,
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id or "",
                    context_id=context.context_id or "",
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id or "",
                    context_id=context.context_id or "",
                    status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
                )
            )

    request_handler = DefaultRequestHandler(
        agent_executor=_BridgeAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    config = A2AConfig(path=route_path)
    return LitestarA2A(
        agent_card=card,
        request_handler=request_handler,
        config=config,
    )
