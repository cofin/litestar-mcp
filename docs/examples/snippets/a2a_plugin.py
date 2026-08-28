from a2a.server.request_handlers import RequestHandler
from a2a.types import AgentCard, AgentInterface
from litestar import Litestar

from litestar_mcp.a2a import LitestarA2A


def create_app(request_handler: RequestHandler) -> Litestar:
    card = AgentCard(
        name="Support agent",
        description="Answers support questions",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url="https://example.com/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
    )
    return Litestar(plugins=[LitestarA2A(card, request_handler)])
