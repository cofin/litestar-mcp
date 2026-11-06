"""Snippet: background queued agent turns with QueuedAgentChatController and ChannelsPlugin."""

# --8<-- [start:example]
from litestar import Litestar
from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend
from litestar.di import Provide
from litestar.stores.memory import MemoryStore
from litestar_queues import QueuePlugin

from litestar_mcp.agent import Agent, AgentRuntime, MockModelClient, ModelDelta
from litestar_mcp.agent.sessions import LitestarStoreSessionStore
from litestar_mcp.queues import (
    AgentRegistry,
    QueuedAgentChatController,
    QueuedTurnManager,
    set_agent_registry,
    set_channels_service,
)

session_store = LitestarStoreSessionStore(MemoryStore())
model_client = MockModelClient(
    responses=[
        [ModelDelta(event_type="delta", text="Background analysis completed.")],
    ],
)
support_agent = Agent(
    name="root",
    description="Background analyst agent running on litestar-queues.",
    instructions="Analyze the user request and report findings.",
    model=model_client,
)
runtime = AgentRuntime(
    target=support_agent,
    sessions=session_store,
)

registry = AgentRegistry({"root": runtime})
set_agent_registry(registry)

channels_plugin = ChannelsPlugin(
    backend=MemoryChannelsBackend(history=1000),
    arbitrary_channels_allowed=True,
)
set_channels_service(channels_plugin)
queue_plugin = QueuePlugin()


def provide_turn_manager() -> QueuedTurnManager:
    """Provide QueuedTurnManager bound to the active QueueService."""
    return QueuedTurnManager(queue_service=queue_plugin.get_service())


app = Litestar(
    route_handlers=[QueuedAgentChatController],
    dependencies={
        "turn_manager": Provide(provide_turn_manager, sync_to_thread=False),
    },
    plugins=[
        channels_plugin,
        queue_plugin,
        runtime,
    ],
)
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
