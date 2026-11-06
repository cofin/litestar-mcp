"""Snippet: LitestarA2A with A2A 1.0 HTTP+JSON REST routes, tenant header resolution, and context_builder."""

# --8<-- [start:example]
from typing import Any
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Message, Part, Role
from a2a.utils.errors import UnsupportedOperationError
from litestar import Litestar, Request, get
from litestar.exceptions import PermissionDeniedException

from litestar_mcp.a2a import A2AConfig, LitestarA2A

ALLOWED_TENANTS = frozenset({"acme", "globex"})


@get(
    "/tickets/{ticket_id:str}",
    opt={
        "a2a_skill": {
            "id": "ticket_lookup",
            "name": "Ticket Lookup",
            "description": "Retrieve tenant support ticket status by identifier.",
            "tags": ["support", "tickets"],
        }
    },
)
async def get_ticket(ticket_id: str) -> dict[str, str]:
    """Retrieve a tenant support ticket by identifier."""
    return {"ticket_id": ticket_id, "status": "open"}


class TenantSupportExecutor(AgentExecutor):
    """Execute A2A requests within the validated tenant context."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Enqueue a tenant-scoped agent response message."""
        tenant = context.call_context.tenant if context.call_context is not None else "default"
        reply_text = f"[{tenant}] Processed: {context.get_user_input()}"
        await event_queue.enqueue_event(
            Message(
                message_id=str(uuid4()),
                role=Role.ROLE_AGENT,
                parts=[Part(text=reply_text)],
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Report cancellation as unsupported for stateless message responses."""
        raise UnsupportedOperationError(message="Cancellation is not supported for stateless messages")


async def authorize_tenant(request: Request[Any, Any, Any], context: ServerCallContext) -> ServerCallContext:
    """Validate the resolved A2A tenant from URL path or X-A2A-Tenant header."""
    del request
    if context.tenant not in ALLOWED_TENANTS:
        raise PermissionDeniedException(detail="Authorized tenant is required")
    context.state["tenant"] = context.tenant
    return context


card = AgentCard(
    name="Multi-Tenant Support Agent",
    description="Handles tenant-isolated support inquiries over JSON-RPC 2.0 and HTTP+JSON REST",
    version="1.0.0",
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="ticket_lookup",
            name="Ticket Lookup",
            description="Retrieve tenant support ticket status by identifier.",
            tags=["support", "tickets"],
        )
    ],
    capabilities=AgentCapabilities(streaming=True),
    supported_interfaces=[
        AgentInterface(
            url="https://agents.example.com/a2a",
            protocol_binding="JSONRPC",
            protocol_version="1.0",
        ),
        AgentInterface(
            url="https://agents.example.com/a2a",
            protocol_binding="HTTP+JSON",
            protocol_version="1.0",
        ),
    ],
)

config = A2AConfig(
    path="/a2a",
    enable_http_json=True,
    tenant_header="X-A2A-Tenant",
    route_opt={"requires_tenant": True},
    agent_card_route_opt={"a2a_skill": "public_card"},
    context_builder=authorize_tenant,
)

app = Litestar(
    route_handlers=[get_ticket],
    plugins=[LitestarA2A(card, TenantSupportExecutor(), config)],
)
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
