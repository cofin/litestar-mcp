====================
Tools and Resources
====================

Once marked, a route is ready to be called over MCP. This page walks
through what registration looks like end-to-end, using the task-manager
demo as a reference.

Tool Registration
=================

Every ``mcp_tool`` handler becomes an entry in the plugin registry at
startup. The task-manager demo registers five tool handlers covering the
full CRUD lifecycle:

.. literalinclude:: /examples/task_manager/main.py
    :language: python
    :caption: ``docs/examples/task_manager/main.py`` - ``register_tools``
    :pyobject: register_tools

The handlers themselves are ordinary Litestar ``@get`` / ``@post`` /
``@delete`` callables - the only extra is the ``mcp_tool`` kwarg. Each tool
is discoverable via ``tools/list`` and invocable via ``tools/call``.

Resource Registration
=====================

Resources follow the same pattern with the ``mcp_resource`` kwarg. The
same demo registers two read-only resources:

.. literalinclude:: /examples/task_manager/main.py
    :language: python
    :caption: ``docs/examples/task_manager/main.py`` - ``register_resources``
    :pyobject: register_resources

Marked resources appear in ``resources/list`` and are fetched via
``resources/read``. The plugin always ships one synthetic resource,
``litestar://openapi``, that returns the application's OpenAPI document.

JSON-RPC Round-Trip
===================

The MCP Streamable HTTP transport is a single JSON-RPC endpoint at
``/mcp``. Clients initialise once, then send ``tools/list``, ``tools/call``,
``resources/list``, and ``resources/read`` methods:

.. code-block:: bash

    # Initialise the server
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
           "params":{"protocolVersion":"2025-11-25","capabilities":{},
           "clientInfo":{"name":"curl","version":"1.0"}}}'

    # List every tool marked in the application
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

    # Execute a specific tool (task-manager demo)
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
           "params":{"name":"list_tasks","arguments":{}}}'

    # Read a resource by URI
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":4,"method":"resources/read",
           "params":{"uri":"litestar://openapi"}}'

Successful responses carry the handler's return value inside the standard
JSON-RPC envelope. Errors raised from the underlying handler are mapped
onto JSON-RPC error objects automatically.
