"""A2A 1.0 protocol bridge for Agent and AgentGroup specifications."""

import importlib.util
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
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
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.exceptions import MissingDependencyError

A2A_INSTALLED: bool = importlib.util.find_spec("a2a") is not None
_missing_a2a_exc: ImportError | None = None

if TYPE_CHECKING:
    from collections.abc import Callable

    from a2a.server.context import ServerCallContext

__all__ = ("agent_to_a2a",)


def _default_owner_resolver(context: "ServerCallContext") -> str:
    """Resolve an owner identity string from an A2A ServerCallContext."""
    user = getattr(context, "user", None)
    user_id = getattr(user, "user_id", None) or getattr(user, "id", None)
    tenant = getattr(context, "tenant", None)
    if user_id and tenant:
        return f"{tenant}:{user_id}"
    return str(user_id or tenant or "anonymous")


def agent_to_a2a(
    agent_or_group: Agent | AgentGroup,
    path: str = "/a2a",
    *,
    base_url: str,
    runtime: AgentRuntime | None = None,
    task_store: Any | None = None,
    owner_resolver: "Callable[[ServerCallContext], str] | None" = None,
    security_schemes: dict[str, Any] | None = None,
    security_requirements: list[Any] | None = None,
) -> LitestarA2A:
    """Mount an Agent or AgentGroup as an A2A 1.0 Litestar plugin.

    Args:
        agent_or_group: Single agent or multi-agent group to expose over A2A.
        path: Route path or absolute URL for the A2A JSON-RPC endpoint.
        base_url: Required base URL used to construct the advertised AgentInterface
            when path is a relative route path.
        runtime: Optional AgentRuntime instance used to execute incoming requests.
            Defaults to AgentRuntime(target=agent_or_group).
        task_store: Optional A2A TaskStore override. Defaults to an owner-scoped
            InMemoryTaskStore.
        owner_resolver: Optional callable resolving owner string from ServerCallContext.
        security_schemes: Optional OpenAPI/A2A security schemes for the AgentCard.
        security_requirements: Optional security requirements for the AgentCard.

    Returns:
        Configured LitestarA2A plugin instance.
    """
    if _missing_a2a_exc is not None:
        raise MissingDependencyError(package="a2a-sdk", extra="a2a") from _missing_a2a_exc

    effective_runtime = runtime if runtime is not None else AgentRuntime(target=agent_or_group)
    effective_resolver = owner_resolver or _default_owner_resolver
    effective_task_store = task_store or InMemoryTaskStore(owner_resolver=effective_resolver)

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
    for inst in agent.skill_instances:
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

    card_kwargs: dict[str, Any] = {
        "name": agent_name,
        "description": agent_description,
        "version": "1.0.0",
        "default_input_modes": ["text/plain"],
        "default_output_modes": ["text/plain"],
        "skills": skills_list,
        "capabilities": AgentCapabilities(streaming=True),
        "supported_interfaces": [
            AgentInterface(
                url=interface_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
    }
    if security_schemes:
        card_kwargs["security_schemes"] = security_schemes
    if security_requirements:
        card_kwargs["security_requirements"] = security_requirements

    card = AgentCard(**card_kwargs)

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
            call_context = getattr(context, "call_context", None)
            owner_key = effective_resolver(call_context) if call_context is not None else None
            user_obj = {"id": owner_key} if owner_key else None
            tool_ctx = ToolContext(
                user=user_obj,
                session_id=context.context_id,
                turn_id=context.task_id,
            )

            turn_res = await effective_runtime.run_turn(
                TurnRequest(
                    user_message=user_input,
                    session_id=context.context_id or "a2a-default",
                    turn_id=context.task_id or "a2a-turn",
                    context=tool_ctx,
                )
            )
            answer_text = turn_res.content

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
        task_store=effective_task_store,
        agent_card=card,
    )
    config = A2AConfig(path=route_path)
    return LitestarA2A(
        agent_card=card,
        request_handler=request_handler,
        config=config,
    )
