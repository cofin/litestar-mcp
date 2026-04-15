==============
Authentication
==============

The plugin's auth layer is optional. Without
:class:`~litestar_mcp.auth.MCPAuthConfig` the MCP endpoints are public;
with one attached to :class:`~litestar_mcp.MCPConfig`, every request must
present a bearer token, and the plugin publishes RFC 9728 metadata so
clients can discover how to authenticate.

OAuth Overview
==============

Authentication is plugin-level - it gates the MCP transport itself rather
than individual handlers. Two configurations are supported:

- **Inline validator** - pass a ``token_validator`` callable that returns
  claims on success.
- **OIDC provider** - declare one or more
  :class:`~litestar_mcp.auth.OIDCProviderConfig` entries and the plugin
  fetches JWKS and validates signatures automatically.

Either way, the validated claims are attached to the request so downstream
tools can read the authenticated subject.

Writing a Bearer Validator
==========================

Use an inline validator for test fixtures, API keys, or any custom token
shape. The callable receives the raw bearer token and returns a claims
dictionary when valid, ``None`` otherwise.

.. literalinclude:: /examples/snippets/auth_bearer_validator.py
    :language: python
    :caption: ``docs/examples/snippets/auth_bearer_validator.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Using OIDC Providers
====================

For production workloads, point the plugin at an OIDC issuer instead.
:class:`~litestar_mcp.auth.OIDCProviderConfig` carries the issuer,
audience, and accepted signing algorithms; the plugin handles JWKS
discovery and caching.

.. literalinclude:: /examples/snippets/auth_oidc_provider.py
    :language: python
    :caption: ``docs/examples/snippets/auth_oidc_provider.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Mapping Claims to Users
=======================

The validated claims payload is available on the request's ``state`` and
can be pulled into any handler via ordinary Litestar dependency injection.
Typical patterns:

- Treat ``sub`` as the user identifier.
- Enforce finer-grained access with the ``scope`` or ``scp`` claim.
- Attach tenant metadata through custom claims your issuer provides.

.. note::

    The reference-notes examples (Phase B of the docs parity flow) show
    end-to-end wiring - JWT + Advanced Alchemy, JWT + SQLSpec, and the
    Cloud Run / Google IAP variants. Cross-link from here rather than
    duplicating the claim-mapping code on this page.
