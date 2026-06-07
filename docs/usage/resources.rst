=========
Resources
=========

Resources are read-only payloads such as schemas, capability summaries,
or cached projections. Tag a Litestar route handler with
``mcp_resource="<resource_name>"`` and the plugin publishes it via
``resources/list`` and ``resources/read``. The task-manager demo
registers two:

.. literalinclude:: /examples/task_manager/main.py
    :language: python
    :caption: ``docs/examples/task_manager/main.py`` - ``register_resources``
    :pyobject: register_resources

Marked resources appear in ``resources/list`` and are fetched via
``resources/read``. The plugin always ships one synthetic resource,
``litestar://openapi``, that returns the application's OpenAPI document.

Resource URI Templates
======================

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
completion by default; user-supplied completion is planned but not yet
exposed through a stable API.

JSON-RPC Round-Trip
===================

After ``initialize``, clients drive resources via ``resources/list`` and
``resources/read``:

.. code-block:: bash

    # List every resource marked in the application
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":1,"method":"resources/list","params":{}}'

    # Read a resource by URI
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":2,"method":"resources/read",
           "params":{"uri":"litestar://openapi"}}'

Successful responses carry the handler's return value inside the
standard JSON-RPC envelope.

Error Contract
==============

``resources/read`` for an unknown URI returns MCP's spec-mandated
resource-not-found code ``-32002`` with ``data.uri``. When a marked
handler raises or returns an error during a read, the JSON-RPC ``code``
is ``INTERNAL_ERROR`` (``-32603``) — the ``code`` reflects the
primitive-level error class, never the handler's HTTP status. The
original status is preserved in ``data.statusCode`` so clients can
recover the finer signal:

.. code-block:: json

    {"jsonrpc":"2.0","id":2,"error":{
      "code":-32603,"message":"Resource read failed",
      "data":{"statusCode":503,"content":{"error":"upstream timeout"}}}}

.. note::

    The resource-not-found code ``-32002`` is mandated by the current MCP
    specification. An upstream proposal (SEP-2164) would migrate it to
    ``-32602``; this page will be updated if that lands.
