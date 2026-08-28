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

The default RPC mount is ``/a2a`` and the card is served from
``/.well-known/agent-card.json``. Litestar middleware and configured guards
remain the outer security boundary. The SDK owns JSON-RPC parsing, protocol
version handling, streaming, cancellation, and task lifecycle. MCP routes are
never converted into A2A skills automatically.
