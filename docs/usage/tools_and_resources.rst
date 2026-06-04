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

Resource URI Templates
----------------------

Pass ``mcp_resource_template="scheme://path/{var}"`` alongside
``mcp_resource`` to register an `RFC 6570 Level 1
<https://datatracker.ietf.org/doc/html/rfc6570>`_ URI template. Clients
can then request concrete URIs that match the template, and the plugin
passes the extracted variables straight through to the handler the same
way Litestar would bind path parameters on an HTTP request:

.. literalinclude:: /examples/snippets/resource_template.py
    :language: python
    :caption: ``docs/examples/snippets/resource_template.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Registered templates are announced via the ``resources/templates/list``
JSON-RPC method, and concrete URIs flow through ``resources/read``:

.. code-block:: json

    // Request
    {"jsonrpc":"2.0","id":1,"method":"resources/read",
     "params":{"uri":"app://workspaces/42/files/99"}}

    // Response (extracted vars -> handler kwargs)
    {"jsonrpc":"2.0","id":1,"result":{"contents":[
      {"uri":"app://workspaces/42/files/99","mimeType":"application/json",
       "text":"{\"workspace\":\"42\",\"file\":\"99\"}"}]}}

``{var}`` matches a single non-empty path segment — it does NOT cross
``/``. Ambiguous templates resolve to the first-registered match. The
``completion/complete`` JSON-RPC method is available but returns an empty
completion by default for 0.5.0; a ``@mcp_resource_completion`` decorator
is planned for a future release.

List Pagination
===============

The MCP list methods accept an optional opaque ``cursor`` parameter and
return ``nextCursor`` when another page is available:

- ``tools/list``
- ``resources/list``
- ``resources/templates/list``
- ``prompts/list``

The plugin currently uses a server-selected page size of 100 entries. Treat
``nextCursor`` as an opaque token: pass it back as ``params.cursor`` on the
next request and stop when the response omits ``nextCursor``. Invalid cursor
values return a JSON-RPC ``INVALID_PARAMS`` error (``-32602``).

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
JSON-RPC envelope.

Error Contract
==============

Error mapping follows the MCP primitive being invoked rather than a generic
HTTP status table:

- ``tools/call`` keeps handler validation failures, API failures, and
  business errors inside the tool result with ``isError: true``. Protocol
  problems such as an unknown tool name still use JSON-RPC errors.
- ``prompts/get`` returns ``INVALID_PARAMS`` (``-32602``) for invalid prompt
  names, missing or invalid prompt arguments, and invalid list cursors.
  Handler execution failures return ``INTERNAL_ERROR`` (``-32603``) with
  structured details in the JSON-RPC ``data`` member.
- ``resources/read`` returns MCP's resource-not-found code ``-32002`` with
  ``data.uri`` for unknown resource URIs. Handler read failures return
  ``INTERNAL_ERROR`` (``-32603``) with structured details in ``data``.
