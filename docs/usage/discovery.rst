=========
Discovery
=========

MCP 2026-07-28 uses the mandatory ``server/discover`` request. The old
experimental ``/.well-known/mcp-server.json`` document is not served.

Send discovery to the configured MCP POST endpoint with the same metadata
and routing headers as every other request:

.. code-block:: bash

    curl -X POST http://127.0.0.1:8000/mcp \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -H 'MCP-Protocol-Version: 2026-07-28' \
      -H 'Mcp-Method: server/discover' \
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "curl", "version": "1"}
          }
        }
      }'

The result reports supported protocol versions and implemented capabilities.
Developer-enabled extensions appear under ``capabilities.extensions``; the
server never echoes arbitrary extension declarations.

Separate metadata endpoints
===========================

``GET /.well-known/oauth-protected-resource`` remains the RFC 9728 discovery
document for OAuth configuration. ``GET /.well-known/agent-card.json`` remains
a separate agent metadata card; it does not imply an A2A protocol endpoint.

Both documents are independent of MCP capability discovery. The MCP transport
itself is ``POST`` only.
