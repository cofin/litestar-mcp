============
Stdio Bridge
============

Use the bridge when an MCP client only supports local stdio servers but
your Litestar MCP server is available over Streamable HTTP.

The bridge is a thin transport adapter:

- it reads JSON-RPC messages from local stdin;
- forwards them to the remote ``POST /mcp`` Streamable HTTP endpoint;
- opens the remote ``GET /mcp`` SSE stream after
  ``notifications/initialized``;
- writes remote JSON-RPC messages back to local stdout;
- sends ``DELETE /mcp`` during shutdown when the server issued a session id.

It does not depend on the official ``mcp`` Python SDK. The base package
already depends on ``httpx``; installing the bridge extra adds only
``httpx-sse``.

Install
========

For an installed project:

.. code-block:: bash

    pip install "litestar-mcp[bridge]"

For one-off client configuration, run it through ``uvx``:

.. code-block:: bash

    uvx --from "litestar-mcp[bridge]" litestar-mcp bridge \
        --endpoint https://api.example.com/mcp

If the server publishes ``/.well-known/mcp-server.json``, pass any URL on
the same origin with ``--discover`` and the bridge will read
``endpoints.mcp`` from the manifest:

.. code-block:: bash

    uvx --from "litestar-mcp[bridge]" litestar-mcp bridge \
        --endpoint https://api.example.com \
        --discover

Client Configuration
====================

For stdio-only clients, configure the local command as the MCP server:

.. code-block:: json

    {
      "mcpServers": {
        "remote-litestar": {
          "command": "uvx",
          "args": [
            "--from",
            "litestar-mcp[bridge]",
            "litestar-mcp",
            "bridge",
            "--endpoint",
            "https://api.example.com/mcp"
          ]
        }
      }
    }

If the client supports Streamable HTTP directly, prefer the remote
``https://api.example.com/mcp`` URL instead of the bridge.

Headers and Bearer Tokens
=========================

Static headers can be passed more than once:

.. code-block:: bash

    litestar-mcp bridge \
        --endpoint https://api.example.com/mcp \
        --header "X-Tenant: acme" \
        --header "X-Trace-Source: mcp-client"

For bearer tokens stored in an environment variable:

.. code-block:: bash

    litestar-mcp bridge \
        --endpoint https://api.example.com/mcp \
        --bearer-env MCP_TOKEN

For platforms that expect the token in a non-``Authorization`` header,
override the header name and prefix:

.. code-block:: bash

    litestar-mcp bridge \
        --endpoint https://api.example.com/mcp \
        --bearer-env IAP_JWT \
        --header-name X-Goog-IAP-JWT-Assertion \
        --token-prefix ""

``--bearer-cmd`` runs a local command before each HTTP request and uses
stdout as the token. Prefer ``--bearer-env`` on Windows when possible:
command strings are split with POSIX-style shell parsing. For complex
Windows commands, wrap token lookup in a small script and pass that script
path as ``--bearer-cmd``.

Identity Boundary
=================

The remote Litestar app remains the authorization boundary. The bridge can
attach headers and bearer tokens, but it cannot prove domain ownership or
enforce object-level permissions locally. Put those checks in ordinary
Litestar guards, authentication middleware, or dependencies as described in
:doc:`security`.

Windows Support
===============

No ``pywin32`` dependency is required. The bridge uses the stdin/stdout
pipes inherited from the MCP client and standard-library subprocess support
only for optional token lookup. ``pywin32`` is useful for SDKs that spawn
and manage child stdio server processes on Windows; this bridge does not do
that.

Troubleshooting
===============

``MissingDependencyError`` at startup
    Install ``litestar-mcp[bridge]`` so ``httpx-sse`` is available.

``Unexpected Streamable HTTP content type``
    The server returned neither JSON nor ``text/event-stream``. Check the
    endpoint URL and make sure the remote server is serving the MCP
    Streamable HTTP route.

``401 Unauthorized``
    The bridge retries once with a fresh token. If the second request still
    fails, check the bearer source and remote auth middleware.
