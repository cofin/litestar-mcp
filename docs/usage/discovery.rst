=========
Discovery
=========

The plugin publishes a handful of discovery documents the moment it is
registered. No extra routes or configuration are required - installing
:class:`~litestar_mcp.LitestarMCP` is enough.

Well-Known Endpoints
====================

.. literalinclude:: /examples/snippets/discovery_endpoints.py
    :language: python
    :caption: ``docs/examples/snippets/discovery_endpoints.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

With this setup the following URLs are served automatically:

.. list-table::
    :widths: 40 60
    :header-rows: 1

    * - Endpoint
      - Purpose
    * - ``GET /mcp``
      - MCP Streamable HTTP transport (SSE stream + JSON-RPC requests).
    * - ``GET /.well-known/mcp-server.json``
      - Experimental MCP server manifest.
    * - ``GET /.well-known/agent-card.json``
      - Agent metadata document consumed by MCP-aware clients.
    * - ``GET /.well-known/oauth-protected-resource``
      - RFC 9728 metadata; only registered when auth is configured.

MCP Server Manifest
===================

``/.well-known/mcp-server.json`` describes the server's identity, transport,
and capabilities. A minimal response looks like:

.. code-block:: json

    {
      "name": "litestar-mcp",
      "description": "Litestar MCP plugin",
      "transports": [
        {"type": "streamable-http", "url": "/mcp"}
      ],
      "capabilities": {
        "tools": {"listChanged": false},
        "resources": {"listChanged": false, "subscribe": false}
      }
    }

Agent Card
==========

``/.well-known/agent-card.json`` publishes the same identity in the
``agent-card`` shape used by agent-to-agent discovery:

.. code-block:: json

    {
      "name": "litestar-mcp",
      "description": "Litestar MCP plugin",
      "url": "/mcp",
      "capabilities": {"tools": true, "resources": true}
    }

OAuth Protected Resource
========================

When :class:`~litestar_mcp.auth.MCPAuthConfig` is present, the plugin adds
``/.well-known/oauth-protected-resource`` following RFC 9728:

.. code-block:: json

    {
      "resource": "https://api.example.com/mcp",
      "authorization_servers": ["https://auth.example.com"],
      "scopes_supported": ["mcp:read"],
      "bearer_methods_supported": ["header"]
    }

See :doc:`auth` for how providers and scopes feed into this document.

.. note::

    ``/.well-known/oauth-protected-resource`` is publicly reachable -
    clients discover auth requirements *before* presenting a token. The
    plugin marks the route with ``exclude_from_auth=True`` so Litestar
    guards never gate it.
