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
``/.well-known/agent-card.json``. Litestar owns HTTP, JSON decoding, SSE,
middleware, configured guards, and client-disconnect handling. The SDK supplies
the official protocol models, errors, compatibility conversions,
``RequestHandler`` execution contract, and task lifecycle. The adapter does not
import Starlette, and MCP routes are never converted into A2A skills
automatically.
