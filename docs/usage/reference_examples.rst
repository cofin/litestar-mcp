==================
Reference Examples
==================

The ``docs/examples/notes/`` tree ships a single "notes" application
implemented across a matrix of backend and authentication choices.
Every variant exposes the **same MCP surface** (``list_notes`` /
``create_note`` / ``delete_note`` tools and ``notes_schema`` /
``app_info`` resources), so the only things that differ between
variants are the backend, auth mode, deployment target, and whether
dependency injection is Litestar-native or routed through Dishka.

Use this page to pick a starting point. Each family README is the
authoritative source for its variants — this page only tells you where
to look.

Pick a family
=============

.. grid:: 1 1 2 2
    :gutter: 2
    :padding: 0

    .. grid-item-card:: Advanced Alchemy family
        :link: https://github.com/litestar-org/litestar-mcp/blob/main/docs/examples/notes/advanced_alchemy/README.md

        ORM-flavored service/repository layer on top of SQLAlchemy with
        ``UUIDAuditBase`` audit columns and Litestar session lifecycle
        integration.

        **Variants:** ``no_auth``, ``no_auth_dishka``, ``jwt_auth``,
        ``jwt_auth_dishka``.

        Pick this when you want an ORM surface, audit columns, and
        familiar SQLAlchemy ergonomics.

    .. grid-item-card:: SQLSpec family
        :link: https://github.com/litestar-org/litestar-mcp/blob/main/docs/examples/notes/sqlspec/README.md

        Explicit, typed async SQL with parameterized queries and
        typed result mapping. Hosts the deployment-oriented variants
        (Cloud Run, Google IAP).

        **Variants:** ``no_auth``, ``no_auth_dishka``, ``jwt_auth``,
        ``jwt_auth_dishka``, ``cloud_run_jwt``, ``google_iap``.

        Pick this when you want explicit SQL, first-class async
        adapters, or one of the deployment-focused variants.

Variant matrix
==============

.. list-table::
   :header-rows: 1
   :widths: 18 18 10 22 32

   * - Backend
     - Auth
     - DI
     - Deployment target
     - File
   * - Advanced Alchemy
     - none
     - Litestar
     - local demo
     - ``advanced_alchemy/no_auth.py``
   * - Advanced Alchemy
     - none
     - Dishka
     - local demo
     - ``advanced_alchemy/no_auth_dishka.py``
   * - Advanced Alchemy
     - OAuth2 JWT (HS256)
     - Litestar
     - local / any ASGI
     - ``advanced_alchemy/jwt_auth.py``
   * - Advanced Alchemy
     - OAuth2 JWT (HS256)
     - Dishka
     - local / any ASGI
     - ``advanced_alchemy/jwt_auth_dishka.py``
   * - SQLSpec
     - none
     - Litestar
     - local demo
     - ``sqlspec/no_auth.py``
   * - SQLSpec
     - none
     - Dishka
     - local demo
     - ``sqlspec/no_auth_dishka.py``
   * - SQLSpec
     - OAuth2 JWT (HS256)
     - Litestar
     - local / any ASGI
     - ``sqlspec/jwt_auth.py``
   * - SQLSpec
     - OAuth2 JWT (HS256)
     - Dishka
     - local / any ASGI
     - ``sqlspec/jwt_auth_dishka.py``
   * - SQLSpec
     - OAuth2 JWT (HS256)
     - Litestar
     - Google Cloud Run
     - ``sqlspec/cloud_run_jwt.py``
   * - SQLSpec
     - Google IAP (ES256)
     - Litestar
     - Cloud Run + IAP
     - ``sqlspec/google_iap.py``

Auth mode comparison
====================

The auth story sits on four rungs, each a strict superset of the
trust boundary of the one above it:

no-auth
    Public demo. Notes are shared and not scoped by identity. Use
    for the fastest walkthrough. Do **not** deploy this shape.

JWT (HS256)
    Ordinary application-managed bearer auth. The app owns the
    ``/auth/login`` endpoint, signs its own tokens, and scopes notes
    by the token ``sub`` claim. Backed by
    :class:`litestar.security.jwt.OAuth2PasswordBearerAuth` and
    :class:`~litestar_mcp.auth.MCPAuthConfig`.

Cloud Run JWT
    Same auth model as the plain JWT variant, but with env-driven
    configuration (``CloudRunSettings.from_env()``), an
    unauthenticated ``/healthz`` liveness route, and a companion
    ``Dockerfile.cloud_run`` plus ``.cloud_run.env.example``. Use
    this when the **application** still owns the login story and
    Cloud Run is only the runtime target. This is *not* a Google IAP
    example.

Google IAP
    Identity is managed by Google at the proxy layer. The app only
    validates the signed ``x-goog-iap-jwt-assertion`` header against
    Google's published IAP JWKS (``ES256``). There is no
    ``/auth/login`` endpoint in this variant — IAP is upstream of the
    service and strips client-supplied bearers before requests reach
    Litestar.

Running an example
==================

Every variant is runnable both from a repo checkout (``uv run``) and
as an ephemeral demo without cloning (``uvx``). The canonical
``uvx``-first commands, per-variant extras, and common pitfalls live
in :doc:`uvx_guide`.

.. seealso::

    - :doc:`auth` — full reference for :class:`~litestar_mcp.auth.MCPAuthConfig`,
      OIDC providers, and bearer validators.
    - :doc:`uvx_guide` — ``uvx`` templates, required ``--with`` extras
      per variant, and MCP client config snippets.
