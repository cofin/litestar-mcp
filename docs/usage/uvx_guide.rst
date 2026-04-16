========================
Single-file Run Reference
========================

.. note::

    This page is about **zero-install demo runs**. ``uv run`` (or
    ``uvx run``) reads the :pep:`723` metadata block baked into each
    reference example and provisions an ephemeral environment on first
    invocation. ``uv sync`` inside a checkout is only needed for
    contributors editing the plugin itself.

Run an Example with One Command
===============================

Every runnable example under ``docs/examples/`` declares its
dependencies inline via :pep:`723`. ``uv`` reads the metadata block,
provisions an ephemeral environment, and runs the script — no clone,
no ``uv sync``, no extras juggling:

.. code-block:: bash

    uv run docs/examples/notes/sqlspec/no_auth.py
    uv run docs/examples/notes/advanced_alchemy/jwt_auth.py
    uv run docs/examples/notes/sqlspec/google_iap.py
    uv run docs/examples/notes/sqlspec/cloud_run_jwt.py

Readers who have *not* cloned the repository can pass the raw URL to
``uv run`` instead — ``uv`` will download, cache, and execute the
single file with the declared deps:

.. code-block:: bash

    uv run https://raw.githubusercontent.com/litestar-org/litestar-mcp/main/docs/examples/notes/sqlspec/no_auth.py

Each variant binds the Litestar app to ``http://127.0.0.1:8000`` by
default. MCP clients can point at ``http://127.0.0.1:8000/mcp`` as
soon as the process is up.

Variant matrix
==============

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Family / variant
     - Auth
     - Single-file run command
   * - ``advanced_alchemy/no_auth``
     - none
     - ``uv run docs/examples/notes/advanced_alchemy/no_auth.py``
   * - ``advanced_alchemy/no_auth_dishka``
     - none (Dishka DI)
     - ``uv run docs/examples/notes/advanced_alchemy/no_auth_dishka.py``
   * - ``advanced_alchemy/jwt_auth``
     - HS256 JWT
     - ``uv run docs/examples/notes/advanced_alchemy/jwt_auth.py``
   * - ``advanced_alchemy/jwt_auth_dishka``
     - HS256 JWT (Dishka DI)
     - ``uv run docs/examples/notes/advanced_alchemy/jwt_auth_dishka.py``
   * - ``sqlspec/no_auth``
     - none
     - ``uv run docs/examples/notes/sqlspec/no_auth.py``
   * - ``sqlspec/no_auth_dishka``
     - none (Dishka DI)
     - ``uv run docs/examples/notes/sqlspec/no_auth_dishka.py``
   * - ``sqlspec/jwt_auth``
     - HS256 JWT
     - ``uv run docs/examples/notes/sqlspec/jwt_auth.py``
   * - ``sqlspec/jwt_auth_dishka``
     - HS256 JWT (Dishka DI)
     - ``uv run docs/examples/notes/sqlspec/jwt_auth_dishka.py``
   * - ``sqlspec/cloud_run_jwt``
     - app-managed JWT, env-driven
     - ``uv run docs/examples/notes/sqlspec/cloud_run_jwt.py``
   * - ``sqlspec/google_iap``
     - Google IAP (ES256)
     - ``uv run docs/examples/notes/sqlspec/google_iap.py``

Common pitfalls
===============

JWT variants need a token secret.
    Set ``TOKEN_SECRET`` (or the variant's documented env var) before
    launching. The demo ``/auth/login`` endpoint mints a token against
    that secret for ``POST {"username": ..., "password": ...}``.

Don't confuse Cloud Run JWT with Google IAP.
    ``cloud_run_jwt`` is app-managed HS256 auth that happens to target
    Cloud Run. ``google_iap`` is platform auth where Google manages
    identity upstream of the app. They are separate variants on
    purpose.

Deployment images still build with ``uv sync``.
    The PEP 723 block is for local single-file runs. The shipped
    ``Dockerfile.cloud_run`` for ``sqlspec/cloud_run_jwt.py`` continues
    to build the image with ``uv sync`` against the repo's locked
    environment — that is the expected path for production.

Pointing an MCP client at a running variant
===========================================

Once any variant is running, point an MCP client at
``http://127.0.0.1:8000/mcp``. A Claude Desktop config stanza looks
like:

.. code-block:: json

    {
      "mcpServers": {
        "notes-demo": {
          "url": "http://127.0.0.1:8000/mcp",
          "transport": "http"
        }
      }
    }

An agent framework using a streamable-HTTP MCP client needs only the
same URL; the plugin publishes RFC 9728 discovery at
``http://127.0.0.1:8000/.well-known/oauth-protected-resource`` when an
auth config is attached, so clients can negotiate the bearer flow
automatically.

A bare ``curl`` probe confirms the transport is live:

.. code-block:: bash

    curl -sX POST http://127.0.0.1:8000/mcp \
        -H 'content-type: application/json' \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{}}}' | jq .

When to use ``uvx --from litestar-mcp``
=======================================

``uvx --from litestar-mcp <command>`` still works for the installed-tool
use case — e.g. running a packaged CLI or module that ships with the
distribution. For the reference examples, prefer the single-file
``uv run docs/examples/...`` form above: the PEP 723 block is the
authoritative dependency list and removes the ``--with`` book-keeping.

.. seealso::

    - :doc:`reference_examples` — the family chooser and variant matrix.
    - :doc:`auth` — :class:`~litestar_mcp.auth.MCPAuthConfig` reference.
    - :doc:`deployment` — sticky routing and multi-replica notes.
