====================
``uvx`` Reference Guide
====================

.. note::

    ``uvx`` is for **ephemeral demo runs** — it installs the package
    and its extras into a scratch environment, runs a command, and
    throws the environment away. ``uv run`` is for **development
    inside this repo**. This page is about the first.

Why ``uvx``
===========

The reference notes examples are the primary way most users will first
see the plugin's MCP surface. ``uvx`` lets a reader run any variant
without cloning the repo, without creating a venv, and without editing
a ``pyproject.toml`` — the exact zero-install story we want MCP
clients (Claude Desktop, agents, etc.) to be able to point at.

The command template
====================

Every variant uses the same shape. Only the module path and the
``--with`` extras change:

.. code-block:: bash

    uvx --from litestar-mcp \
        --with "<variant-specific-extras>" \
        python -m docs.examples.notes.<family>.<variant>

``--from litestar-mcp`` pins the package that owns the example tree.
``--with`` declares the adapter-level and auth-level dependencies that
the specific variant imports at runtime. ``python -m ...`` runs the
variant's ``create_app(...)`` bootstrap.

Run any variant
===============

The commands below are the complete, copy-pasteable invocations for
each shipped variant. They all bind the Litestar app to
``http://127.0.0.1:8000`` by default.

Advanced Alchemy family
-----------------------

.. code-block:: bash

    # no-auth (public demo)
    uvx --from litestar-mcp \
        --with "advanced-alchemy,litestar[standard]" \
        python -m docs.examples.notes.advanced_alchemy.no_auth

    # no-auth + Dishka DI
    uvx --from litestar-mcp \
        --with "advanced-alchemy,litestar[standard],dishka" \
        python -m docs.examples.notes.advanced_alchemy.no_auth_dishka

    # app-managed JWT bearer auth
    uvx --from litestar-mcp \
        --with "advanced-alchemy,litestar[standard],pyjwt" \
        python -m docs.examples.notes.advanced_alchemy.jwt_auth

    # JWT + Dishka DI
    uvx --from litestar-mcp \
        --with "advanced-alchemy,litestar[standard],pyjwt,dishka" \
        python -m docs.examples.notes.advanced_alchemy.jwt_auth_dishka

SQLSpec family
--------------

.. code-block:: bash

    # no-auth (public demo)
    uvx --from litestar-mcp \
        --with "sqlspec[aiosqlite],litestar[standard]" \
        python -m docs.examples.notes.sqlspec.no_auth

    # no-auth + Dishka DI
    uvx --from litestar-mcp \
        --with "sqlspec[aiosqlite],litestar[standard],dishka" \
        python -m docs.examples.notes.sqlspec.no_auth_dishka

    # app-managed JWT bearer auth
    uvx --from litestar-mcp \
        --with "sqlspec[aiosqlite],litestar[standard],pyjwt" \
        python -m docs.examples.notes.sqlspec.jwt_auth

    # JWT + Dishka DI
    uvx --from litestar-mcp \
        --with "sqlspec[aiosqlite],litestar[standard],pyjwt,dishka" \
        python -m docs.examples.notes.sqlspec.jwt_auth_dishka

    # Cloud Run JWT (env-driven config, /healthz)
    uvx --from litestar-mcp \
        --with "sqlspec[aiosqlite],litestar[standard],pyjwt" \
        python -m docs.examples.notes.sqlspec.cloud_run_jwt

    # Google IAP (ES256 signed assertion validation)
    uvx --from litestar-mcp \
        --with "sqlspec[aiosqlite],litestar[standard],pyjwt,cryptography" \
        python -m docs.examples.notes.sqlspec.google_iap

Common pitfalls
===============

``--with`` must include every adapter and auth extra the variant imports.
    The table below is the authoritative list. Missing an extra fails
    with an ``ImportError`` at startup, not at request time.

    .. list-table::
       :header-rows: 1
       :widths: 45 55

       * - Variant
         - Required ``--with`` extras
       * - ``advanced_alchemy/no_auth``
         - ``advanced-alchemy,litestar[standard]``
       * - ``advanced_alchemy/no_auth_dishka``
         - ``advanced-alchemy,litestar[standard],dishka``
       * - ``advanced_alchemy/jwt_auth``
         - ``advanced-alchemy,litestar[standard],pyjwt``
       * - ``advanced_alchemy/jwt_auth_dishka``
         - ``advanced-alchemy,litestar[standard],pyjwt,dishka``
       * - ``sqlspec/no_auth``
         - ``sqlspec[aiosqlite],litestar[standard]``
       * - ``sqlspec/no_auth_dishka``
         - ``sqlspec[aiosqlite],litestar[standard],dishka``
       * - ``sqlspec/jwt_auth``
         - ``sqlspec[aiosqlite],litestar[standard],pyjwt``
       * - ``sqlspec/jwt_auth_dishka``
         - ``sqlspec[aiosqlite],litestar[standard],pyjwt,dishka``
       * - ``sqlspec/cloud_run_jwt``
         - ``sqlspec[aiosqlite],litestar[standard],pyjwt``
       * - ``sqlspec/google_iap``
         - ``sqlspec[aiosqlite],litestar[standard],pyjwt,cryptography``

The JWT variants need a token secret.
    Set ``TOKEN_SECRET`` (or the variant's documented env var) before
    launching. The demo ``/auth/login`` endpoint will mint a token
    against that secret for ``POST {"username": ..., "password": ...}``.

``uvx`` is not ``uv run``.
    If you are working inside a clone of the repo, prefer
    ``uv run python -m docs.examples.notes.<family>.<variant>`` — that
    uses the repo's locked environment. ``uvx`` is for readers who
    have *not* cloned the repo.

Don't confuse Cloud Run JWT with Google IAP.
    ``cloud_run_jwt`` is app-managed HS256 auth that happens to target
    Cloud Run. ``google_iap`` is platform auth where Google manages
    identity upstream of the app. They are separate variants on
    purpose.

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

.. seealso::

    - :doc:`reference_examples` — the family chooser and variant matrix.
    - :doc:`auth` — :class:`~litestar_mcp.auth.MCPAuthConfig` reference.
