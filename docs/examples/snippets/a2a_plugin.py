from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Message, Part, Role
from a2a.utils.errors import UnsupportedOperationError
from litestar import Litestar

from litestar_mcp.a2a import LitestarA2A


class EchoExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            Message(message_id=str(uuid4()), role=Role.ROLE_AGENT, parts=[Part(text=context.get_user_input())])
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(message="This agent returns messages without creating tasks")


def build() -> Litestar:
    card = AgentCard(
        name="Support agent",
        description="Answers support questions",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="echo", name="Echo", description="Repeat a message", tags=["example"])],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                url="https://example.com/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
    )
    handler = DefaultRequestHandler(agent_executor=EchoExecutor(), task_store=InMemoryTaskStore(), agent_card=card)
    return Litestar(plugins=[LitestarA2A(card, handler)])
