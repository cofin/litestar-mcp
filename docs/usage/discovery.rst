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
    :pyobject: build

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

``/.well-known/mcp-server.json`` describes the server's identity, endpoints,
and capabilities. A minimal response looks like:

.. code-block:: json

    {
      "experimental": true,
      "name": "litestar-mcp",
      "version": "1.0.0",
      "protocolVersion": "2025-11-25",
      "endpoints": {
        "mcp": "/mcp",
        "oauthProtectedResource": "/.well-known/oauth-protected-resource",
        "agentMetadata": "/.well-known/agent-card.json"
      },
      "capabilities": {
        "tools": {"listChanged": true},
        "resources": {"subscribe": true, "listChanged": true},
        "tasks": false
      },
      "tools": [],
      "resources": [],
      "prompts": []
    }

The ``protocolVersion`` value mirrors the
:data:`litestar_mcp.manifests.MCP_PROTOCOL_VERSION` constant and will move
in lock-step with the implementation; do not pin against the string above.

The ``prompts`` capability is **gated**: it is only advertised — both in
this manifest and in ``initialize``'s capability response — when at least
one visible prompt is registered. That is why the minimal capabilities
block above omits ``prompts`` entirely while the top-level ``prompts``
array is still present (empty). This matches the MCP spec's
recommendation that servers only declare capabilities for primitives they
actually expose. The same per-tag and per-operation include/exclude
filters that apply to tools and resources also gate prompt visibility.

Agent Metadata Card
===================

``/.well-known/agent-card.json`` publishes the same identity in a format that provides agent metadata for MCP-aware clients:

.. code-block:: json

    {
      "name": "litestar-mcp",
      "description": "Litestar MCP plugin",
      "url": "/mcp",
      "capabilities": {"streaming": true, "mcp": true, "tasks": false},
      "skills": []
    }

.. note::
    MCP server discovery, generic agent metadata, and full Agent-to-Agent (A2A) protocol support are separate concerns.
    Serving this metadata card does not make the server A2A-protocol compatible. A2A protocol support requires a dedicated A2A service endpoint and is tracked separately.


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
