=====
Tools
=====

Tools are executable operations — anything that takes arguments and
returns structured output. Tag a Litestar route handler with
``mcp_tool="<tool_name>"`` and the plugin publishes it via ``tools/list``
and ``tools/call``. The task-manager demo registers five tool handlers
covering the full CRUD lifecycle:

.. literalinclude:: /examples/task_manager/main.py
    :language: python
    :caption: ``docs/examples/task_manager/main.py`` - ``register_tools``
    :pyobject: register_tools

The handlers themselves are ordinary Litestar ``@get`` / ``@post`` /
``@delete`` callables — the only extra is the ``mcp_tool`` kwarg. Each
tool is discoverable via ``tools/list`` and invocable via
``tools/call``.

Tool arguments are validated against the handler's ``parsed_fn_signature``
before dispatch — the same model Litestar uses for ordinary HTTP request
parsing. Missing required arguments surface as JSON-RPC
``INVALID_PARAMS`` (``-32602``). ``Annotated[T, Parameter(...)]`` query
arguments are unwrapped and their ``Parameter`` constraints
(``ge`` / ``le`` / ``min_length`` / ``pattern`` / …) flow through into
the advertised ``inputSchema``.

JSON-RPC Round-Trip
===================

After ``initialize``, clients drive tools via ``tools/list`` and
``tools/call``:

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

Successful responses carry the handler's return value inside the
standard JSON-RPC envelope.

Error Contract
==============

Tool errors are reported differently from the other primitives. A handler
that raises or returns an error response is surfaced **inside the tool
result** with ``isError: true`` (and the detail in ``content``), not as a
JSON-RPC ``error`` object — this lets the model see and react to the
failure. Only protocol-level problems (an unknown tool name, malformed
params) use a JSON-RPC ``error`` with ``INVALID_PARAMS`` (``-32602``).
See :doc:`prompts` and :doc:`resources` for how those primitives map the
handler's HTTP status onto JSON-RPC codes.
