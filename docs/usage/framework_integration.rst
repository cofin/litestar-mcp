=====================
Framework Integration
=====================

The plugin is a first-class Litestar citizen. It participates in the
plugin system, works alongside guards and middleware, and surfaces
through OpenAPI when asked.

Plugin Ordering
===============

Register :class:`~litestar_mcp.LitestarMCP` in your application's
``plugins`` list. Ordering matters when other plugins touch the same
route metadata - register MCP **after** plugins that mutate handlers
(e.g. DI or serialization plugins) so their mutations are visible at
discovery time.

.. literalinclude:: /examples/snippets/framework_litestar.py
    :language: python
    :caption: ``docs/examples/snippets/framework_litestar.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

OpenAPI Integration
===================

Marked routes appear in the OpenAPI schema just like any other handler.
The MCP routes themselves (``/mcp`` and ``/.well-known/*``) are hidden by
default - pass ``include_in_schema=True`` on
:class:`~litestar_mcp.MCPConfig` to expose them.

The plugin also uses your OpenAPI ``security`` declarations to populate
``/.well-known/oauth-protected-resource`` automatically: if your app uses
``OAuth2PasswordBearerAuth`` or publishes an ``OAuth2`` security scheme,
the RFC 9728 metadata picks up the scopes without additional config.

Guards on MCP Routes
====================

Attach Litestar guards to the MCP router by passing them through
:class:`~litestar_mcp.MCPConfig`:

.. code-block:: text

    MCPConfig(guards=[my_guard])

Guards run on ``/mcp`` exactly as they do for any other handler. The
well-known discovery endpoints intentionally bypass guards so clients can
negotiate authentication before presenting a token (see :doc:`discovery`).

Custom Base Path
================

By default the transport is served at ``/mcp``. Override this with
``base_path`` on :class:`~litestar_mcp.MCPConfig` when mounting the plugin
under an API prefix:

- ``MCPConfig(base_path="/api/mcp")`` publishes the transport at
  ``/api/mcp`` and emits discovery documents that advertise the same URL.
- Well-known documents always live under ``/.well-known/*`` regardless of
  ``base_path``; that is part of the RFC.

Filtering Exposure
==================

Use the ``include_tags`` / ``exclude_tags`` and
``include_operations`` / ``exclude_operations`` options to restrict which
marked handlers are visible. Filters apply at ``tools/list`` and
``resources/list`` time - a handler hidden by a filter is simply not
returned, but may still be invoked directly if the caller knows its name.
Precedence is ``exclude > include`` and ``tags > operations``.
