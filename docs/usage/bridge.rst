============
Stdio Bridge
============

Use the bridge when an MCP client only supports local stdio servers and your
Litestar app exposes MCP over Streamable HTTP.

Run the bridge through Litestar's app-bound CLI:

.. code-block:: bash

    litestar --app my_app:app mcp bridge

The command loads the same app object as the rest of the Litestar CLI, finds
the installed :class:`~litestar_mcp.LitestarMCP` plugin, and proxies local
stdio JSON-RPC to that app's MCP endpoint.

The bridge is a thin transport adapter:

- it reads JSON-RPC messages from local stdin;
- adds MCP 2026-07-28 request metadata and standard/custom routing headers;
- forwards independent messages concurrently to the target ``POST`` endpoint;
- multiplexes ``subscriptions/listen`` POST-response streams;
- writes remote JSON-RPC messages back to local stdout;
- maps stdio cancellation to closure of the matching HTTP response stream;
- lazily caches tool schemas used for ``Mcp-Param-*`` headers.

It does not depend on the official ``mcp`` Python SDK. The base package
already depends on ``httpx``; installing the bridge extra adds only
``httpx-sse``.

Install
=======

Install the bridge extra in the same environment as your Litestar app:

.. code-block:: bash

    pip install "litestar-mcp[bridge]"

or:

.. code-block:: bash

    uv add "litestar-mcp[bridge]"

Default Endpoint
================

By default, ``litestar --app my_app:app mcp bridge`` targets:

.. code-block:: text

    http://127.0.0.1:8000{MCPConfig.base_path}

If ``LITESTAR_HOST`` or ``LITESTAR_PORT`` are set, the command uses those
values for the origin. The path always comes from the loaded app's
``LitestarMCP`` plugin configuration:

.. literalinclude:: /examples/snippets/bridge_custom_base_path.py
    :language: python
    :caption: ``docs/examples/snippets/bridge_custom_base_path.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

With that app, the default bridge endpoint is
``http://127.0.0.1:8000/api/mcp``.

Use ``--base-url`` to change the origin while preserving the configured MCP
base path:

.. code-block:: bash

    litestar --app my_app:app mcp bridge \
        --base-url https://app.example.com

Use ``--endpoint`` only when you need a full URL override:

.. code-block:: bash

    litestar --app my_app:app mcp bridge \
        --endpoint https://app.example.com/custom/mcp

``--endpoint`` wins over ``--base-url`` and ``MCPConfig.base_path``.

Client Configuration
====================

For stdio-only clients, configure the Litestar CLI command as the MCP server.
Run it from an environment where your application package and
``litestar-mcp[bridge]`` are installed:

.. code-block:: json

    {
      "mcpServers": {
        "my-litestar-app": {
          "command": "litestar",
          "args": [
            "--app",
            "my_app:app",
            "mcp",
            "bridge",
            "--base-url",
            "http://127.0.0.1:8000"
          ]
        }
      }
    }

If the client supports Streamable HTTP directly, prefer the app's HTTP MCP
URL instead of the bridge.

Headers and Bearer Tokens
=========================

The bridge does not infer credentials from application auth metadata. Pass
headers and token sources explicitly.

Static headers can be passed more than once:

.. code-block:: bash

    litestar --app my_app:app mcp bridge \
        --header "X-Tenant: acme" \
        --header "X-Trace-Source: mcp-client"

For bearer tokens stored in an environment variable:

.. code-block:: bash

    litestar --app my_app:app mcp bridge \
        --bearer-env MCP_TOKEN

For platforms that expect the token in a non-``Authorization`` header,
override the header name and prefix:

.. code-block:: bash

    litestar --app my_app:app mcp bridge \
        --bearer-env IAP_JWT \
        --header-name X-Goog-IAP-JWT-Assertion \
        --token-prefix ""

``--bearer-cmd`` runs a local command before each HTTP request and uses
stdout as the token. Prefer ``--bearer-env`` on Windows when possible:
command strings are split with POSIX-style shell parsing. For complex
Windows commands, wrap token lookup in a small script and pass that script
path as ``--bearer-cmd``.

Timeouts
========

``--timeout`` bounds ordinary HTTP connection setup, writes, and pool
acquisition. Long-lived SSE streams use ``--sse-read-timeout`` instead;
the default allows 300 seconds between server events. Set
``--sse-read-timeout 0`` to disable quiet-period timeouts for idle streams.

Memory Bounds
=============

The bridge does not retain message history. It reads stdin one
newline-delimited JSON-RPC message at a time and streams server responses as
JSON or SSE events.

To prevent a malformed client from growing memory indefinitely by sending a
message without a newline, the bridge caps each stdin JSON-RPC message at
16 MiB by default:

.. code-block:: bash

    litestar --app my_app:app mcp bridge \
        --max-message-size 16777216

Set ``--max-message-size -1`` to disable this per-message cap.

Identity Boundary
=================

The target Litestar app remains the authorization boundary. The bridge can
attach headers and bearer tokens, but it cannot prove domain ownership or
enforce object-level permissions locally. Put those checks in ordinary
Litestar guards, authentication middleware, or dependencies as described in
:doc:`security`.

Windows Support
===============

On Windows, the bridge uses inherited stdin/stdout pipes. For token lookup,
prefer ``--bearer-env`` or wrap platform-specific logic in a small script and
pass it with ``--bearer-cmd``.

Troubleshooting
===============

``MissingDependencyError`` at startup
    Install ``litestar-mcp[bridge]`` so ``httpx-sse`` is available.

``Unexpected Streamable HTTP content type``
    The server returned neither JSON nor ``text/event-stream``. Check the
    endpoint URL and make sure the target app is serving the MCP Streamable
    HTTP route.

``401 Unauthorized``
    The bridge retries once with a fresh token. If the second request still
    fails, check the bearer source and target auth middleware.
