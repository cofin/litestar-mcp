===============
A2A Integration
===============

Install the optional official SDK dependency:

.. code-block:: bash

    pip install "litestar-mcp[a2a]"

Build an official :class:`a2a.types.AgentCard` whose JSON-RPC 1.0 interface
points at the configured mount, then supply an official
:class:`a2a.server.RequestHandler`:

.. literalinclude:: /examples/snippets/a2a_plugin.py
    :language: python
    :caption: Official A2A SDK adapter

The default RPC endpoint is ``/a2a`` and the card is served from
``/.well-known/agent-card.json`` with ``ETag`` and ``Cache-Control`` headers
(``A2AConfig.agent_card_max_age``). Both routes are exempt from CSRF and hidden
from the OpenAPI schema unless ``A2AConfig.include_in_schema`` is set. Requests
must send ``A2A-Version: 1.0``; the official ``ClientFactory`` does so, and a
missing header is treated as protocol 0.3 exactly as the SDK does. Litestar
owns HTTP, JSON decoding, SSE, middleware, configured guards, and
client-disconnect handling. The SDK supplies
the official protocol models, errors, compatibility conversions,
``RequestHandler`` execution contract, and task lifecycle. The adapter does not
import Starlette, and MCP routes are never converted into A2A skills
automatically.
