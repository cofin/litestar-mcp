==============
Authentication
==============

Authentication for the MCP endpoint is a **Litestar middleware** concern.
Apps that already have an authentication middleware (JWT backends,
Google IAP, custom token validators) get MCP authentication for free —
the middleware populates ``request.user`` and ``request.auth`` before
any route handler runs, including MCP tool handlers.

:class:`~litestar_mcp.auth.MCPAuthConfig` is metadata-only: it
describes the auth surface advertised by
``/.well-known/oauth-protected-resource`` so MCP clients can discover
how to obtain a token. Enforcement lives in whichever middleware you
install on the app.

Three Integration Paths
=======================

**Path A — Bring your own auth middleware.**
Use this when your Litestar app already ships an
:class:`~litestar.middleware.authentication.AbstractAuthenticationMiddleware`
(or Litestar's built-in JWT backends). MCP tool handlers inherit
``request.user`` / ``request.auth`` automatically. No ``MCPAuthBackend``
needed. See ``docs/examples/notes/sqlspec/google_iap.py``.

**Path B — Built-in MCPAuthBackend.**
Install
:class:`~litestar_mcp.auth.MCPAuthBackend` via ``DefineMiddleware``.
It validates bearer tokens against OIDC providers and/or a custom
``token_validator``, then populates ``connection.user`` via an optional
``user_resolver``. See ``docs/examples/notes/sqlspec/cloud_run_jwt.py``.

.. literalinclude:: /examples/snippets/auth_bearer_validator.py
    :language: python
    :caption: ``MCPAuthBackend`` with a custom validator
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

**Path C — MCPAuthBackend with OIDC providers.**
For production OIDC workloads, pass one or more
:class:`~litestar_mcp.auth.OIDCProviderConfig` entries. The backend
handles JWKS discovery, caching, and signature verification.

.. literalinclude:: /examples/snippets/auth_oidc_provider.py
    :language: python
    :caption: ``MCPAuthBackend`` with OIDC auto-discovery
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Composable OIDC Factory
========================

:func:`~litestar_mcp.auth.create_oidc_validator` returns an async
callable that validates a single token against an OIDC issuer. Pass it
as ``MCPAuthBackend(token_validator=...)`` or use it inside your own
middleware. Both ``clock_skew`` and ``jwks_cache_ttl`` are configurable.

Injectable JWKS Cache
=====================

:class:`~litestar_mcp.auth.JWKSCache` is a protocol-shaped seam for
apps that already run their own JWKS / OIDC discovery cache. Pass a
shared instance to every validator to avoid redundant network fetches:

.. literalinclude:: /examples/snippets/jwks_cache_shared.py
    :language: python
    :caption: ``docs/examples/snippets/jwks_cache_shared.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

When no cache is passed, the validator uses a process-wide default —
matching 0.4.0 behaviour — so existing apps need no code changes. Any
object implementing ``async get`` / ``async set(*, ttl=int)`` /
``async invalidate`` satisfies the protocol, so a Redis-backed or
application-specific cache can drop in cleanly.

Authorization via Guards
========================

Scopes declared on ``@mcp_tool(scopes=[...])`` are **discovery
metadata only** — they surface under
``tools[].annotations.scopes`` in ``tools/list`` and in
``/.well-known/oauth-protected-resource``. MCP tool dispatch does not
enforce scopes inline; attach a Litestar :class:`~litestar.types.Guard`
to the route / router / controller for authorization. Guards receive
the same :class:`~litestar.connection.ASGIConnection` that an HTTP
request does, so existing ``requires_x`` guards work unchanged on MCP:

.. literalinclude:: /examples/snippets/authorization_guard.py
    :language: python
    :caption: ``docs/examples/snippets/authorization_guard.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Discovery Metadata
==================

When :class:`~litestar_mcp.auth.MCPAuthConfig` is attached to
:class:`~litestar_mcp.MCPConfig`, the plugin publishes
``/.well-known/oauth-protected-resource`` with the configured
``issuer``, ``audience``, and ``scopes``. This endpoint is always
unauthenticated (via Litestar's ``exclude_from_auth`` opt key) so
clients can bootstrap their auth flow.

Mapping Claims to Users
=======================

Middleware populates ``request.auth`` with the validated claims dict
and ``request.user`` with the resolved user object (if a
``user_resolver`` is configured). Tool handlers access these via
normal Litestar DI:

- Read ``request.user`` directly in the handler signature.
- Write a ``Provide(...)`` dependency that extracts the identity from
  ``request.user`` and returns a domain type.
- Enforce scopes or roles via guards that inspect ``request.auth``.

See the reference-notes examples for end-to-end wiring across JWT,
Dishka, Advanced Alchemy, and Google IAP variants.
