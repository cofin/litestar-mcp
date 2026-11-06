"""Snippet: LitestarA2A plugin registration with an AgentCard and AgentExecutor."""

# --8<-- [start:example]
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Message, Part, Role
from a2a.utils.errors import UnsupportedOperationError
from litestar import Litestar

from litestar_mcp.a2a import LitestarA2A


class EchoExecutor(AgentExecutor):
    """Echo incoming user input back as an A2A agent message."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Enqueue an echo response message."""
        await event_queue.enqueue_event(
            Message(message_id=str(uuid4()), role=Role.ROLE_AGENT, parts=[Part(text=context.get_user_input())])
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Reject cancellation for stateless echo messages."""
        raise UnsupportedOperationError(message="This agent returns messages without creating tasks")


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

app = Litestar(plugins=[LitestarA2A(card, EchoExecutor())])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
