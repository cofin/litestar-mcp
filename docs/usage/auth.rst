==============
Authentication
==============

Authentication for the MCP endpoint is a **Litestar middleware** concern.
Whatever authentication middleware the application installs (Litestar's
JWT backends, a Google IAP validator, an API-key check) runs before any
route handler, so MCP tool handlers inherit ``request.user`` and
``request.auth`` exactly as HTTP handlers do. litestar-mcp ships no token
validator, JWKS cache, or discovery document of its own.

Authentication only establishes caller identity. See :doc:`security` for
object-level authorization patterns, transport identity boundaries, and safe
file/path argument guidance.

Bring Your Own Middleware
=========================

Subclass
:class:`~litestar.middleware.authentication.AbstractAuthenticationMiddleware`
(or register one of Litestar's built-in JWT backends) and install it on the
app. The middleware populates ``request.user`` / ``request.auth`` before the
MCP JSON-RPC handler dispatches a tool, so the plugin needs no
authentication configuration of its own:

.. literalinclude:: /examples/snippets/auth_middleware.py
    :language: python
    :caption: ``docs/examples/snippets/auth_middleware.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Litestar's :class:`~litestar.security.jwt.OAuth2PasswordBearerAuth` works the
same way: pass the backend's ``on_app_init`` hook to
:class:`~litestar.app.Litestar` and the MCP routes are guarded like every
other route. ``docs/examples/notes/sqlspec/jwt_auth.py`` is a complete JWT
example.

litestar-security
=================

Applications that standardise on litestar-security keep the same shape:
install the app's ``SecurityPlugin``, then name the mechanism that guards
MCP by passing an opt-based policy through
``MCPConfig(route_opt={"auth": required("api-key")})`` (``required`` is
exported from ``litestar_security``). litestar-mcp registers its routes and
forwards the opt mapping; it builds no verifier. Guards and dependencies on
tool handlers read ``connection.user`` (a ``Principal``) and
``connection.auth`` (a ``SecurityContext``) as they would on any other
route.

MCP clients discover the authorization server through the
``WWW-Authenticate: Bearer resource_metadata=...`` challenge. litestar-security
emits that header only when the mechanism declares
``security_scheme=SecurityScheme(type="http", scheme="bearer")`` and only
for evaluator failures, not for guard failures. Publish the RFC 9728
protected-resource document with ``SecurityConfig.protected_resource``
(a ``ProtectedResourceConfig``). JWKS caching, service tokens, and Google IAP
support are configured on litestar-security, not on the MCP plugin.

Identity Proxies
================

Identity proxies such as Google IAP verify the caller and forward a signed
assertion in a custom header (``x-goog-iap-jwt-assertion`` for IAP). Validate
that header in your own middleware, as
``docs/examples/notes/sqlspec/google_iap.py`` does with a plain
``AbstractAuthenticationMiddleware`` subclass, or configure
litestar-security's IAP support.

Authorization via Guards
========================

Scopes declared on ``@mcp_tool(scopes=[...])`` are **discovery
metadata only** — they surface under ``tools[].annotations.scopes`` in
``tools/list``. MCP tool dispatch does not enforce scopes inline; attach a
Litestar :class:`~litestar.types.Guard` to the route / router / controller
for authorization. Guards receive the same
:class:`~litestar.connection.ASGIConnection` that an HTTP request does, so
existing ``requires_x`` guards work unchanged on MCP:

.. literalinclude:: /examples/snippets/authorization_guard.py
    :language: python
    :caption: ``docs/examples/snippets/authorization_guard.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Mapping Claims to Users
=======================

- Read ``request.user`` directly in the handler signature.
- Write a ``Provide(...)`` dependency that extracts the identity from
  ``request.user`` and returns a domain type.
- Enforce scopes or roles via guards that inspect ``request.auth``.

Stdio Transports
================

In-process stdio (``litestar mcp stdio`` and ``MCP.run(transport="stdio")``)
dispatches every frame through the same ASGI app, so the same middleware
runs and tool handlers see the same ``request.user`` / ``request.auth``.
Standalone apps that run without an authentication middleware seed the
caller identity with ``MCPStdioContext`` instead; see :doc:`standalone_app`.
