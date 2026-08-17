=============
Configuration
=============

The Litestar MCP plugin is configured through :class:`~litestar_mcp.MCPConfig`.
This page walks through each knob the plugin exposes, from a default
registration to task-lifecycle support.

Minimal Setup
=============

The plugin registers with sensible defaults when no configuration is passed.
Every marked route is picked up and served from ``/mcp``.

.. literalinclude:: /examples/snippets/configuration_minimal.py
    :language: python
    :caption: ``docs/examples/snippets/configuration_minimal.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Custom Configuration
====================

Override the base path, server name, or OpenAPI visibility via
:class:`~litestar_mcp.MCPConfig`.

.. literalinclude:: /examples/snippets/configuration_custom.py
    :language: python
    :caption: ``docs/examples/snippets/configuration_custom.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Auth-Enabled Configuration
==========================

Attach an :class:`~litestar_mcp.auth.MCPAuthConfig` to require bearer tokens
on MCP endpoints and publish ``/.well-known/oauth-protected-resource``.
See :doc:`auth` for the full authentication story.

.. literalinclude:: /examples/snippets/configuration_auth.py
    :language: python
    :caption: ``docs/examples/snippets/configuration_auth.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Standalone Prompts
==================

Prompt callables that are not bound to an HTTP route are registered by
passing them to ``LitestarMCP(prompts=[...])``. Each function must first
be decorated with :func:`~litestar_mcp.mcp_prompt`; the plugin rejects
plain callables to keep prompt metadata explicit.

.. literalinclude:: /examples/snippets/configuration_prompts.py
    :language: python
    :caption: ``docs/examples/snippets/configuration_prompts.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

See :doc:`marking_routes` for the handler-based ``mcp_prompt`` opt-key
form, which routes a prompt under HTTP *and* publishes it via
``prompts/get``.

Task Lifecycle
==============

Enable the opt-in ``io.modelcontextprotocol/tasks`` extension by passing an
:class:`~litestar_mcp.config.MCPTaskConfig`. Task records use a Litestar
Store; the default in-memory Store is intended for development.

.. literalinclude:: /examples/snippets/configuration_tasks.py
    :language: python
    :caption: ``docs/examples/snippets/configuration_tasks.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Configuration Options
=====================

.. list-table::
    :widths: 25 25 50
    :header-rows: 1

    * - Option
      - Default
      - Description
    * - ``base_path``
      - ``"/mcp"``
      - Base path for the MCP Streamable HTTP endpoint.
    * - ``include_in_schema``
      - ``False``
      - Whether to include MCP routes in the OpenAPI schema.
    * - ``name``
      - ``None``
      - Server name override (falls back to the OpenAPI title).
    * - ``guards``
      - ``None``
      - Litestar guards applied to the MCP router.
    * - ``route_opt``
      - ``None``
      - ``opt`` mapping merged into the two route groups the plugin owns (the
        ``/mcp`` router and the ``.well-known`` discovery handlers); your own
        ``@mcp_tool`` / ``@mcp_resource`` handlers are untouched. Keys win over
        the plugin defaults, letting an ``opt``-based auth policy be stamped
        onto the MCP surface. The discovery handlers default to
        ``{"exclude_from_auth": True}`` when no override is supplied.
    * - ``allowed_origins``
      - ``None``
      - Restrict accepted ``Origin`` header values.
    * - ``include_operations`` / ``exclude_operations``
      - ``None``
      - Filter exposure by Litestar operation name.
    * - ``include_tags`` / ``exclude_tags``
      - ``None``
      - Filter exposure by OpenAPI tags.
    * - ``auth``
      - ``None``
      - Enable bearer-token validation and OAuth protected-resource metadata.
    * - ``tasks``
      - ``False``
      - Enable the Tasks extension, optionally with a persistent Store.
    * - ``cache_ttl_ms`` / ``cache_scope``
      - ``0`` / ``"private"``
      - Cache hints on discovery, list, and resource-read results.
    * - ``subscription_max_streams``
      - ``10000``
      - Maximum concurrent ``subscriptions/listen`` response streams.
    * - ``subscription_keepalive_seconds``
      - ``15.0``
      - Seconds between subscription keepalive comments.
    * - ``subscription_channels``
      - ``None``
      - Optional configured Litestar ``ChannelsPlugin`` for cross-worker fan-out.
    * - ``before_tool_call``
      - ``None``
      - Optional callback invoked once before each ``tools/call`` dispatch.
    * - ``after_tool_call``
      - ``None``
      - Optional callback invoked once after each ``tools/call`` dispatch
        with the result or exception and elapsed duration.
    * - ``list_page_size``
      - ``100``
      - Server-chosen page size for the ``*/list`` methods (see below).
    * - ``max_blob_bytes``
      - ``25 * 1024 * 1024``
      - Maximum raw byte size for embedded MCP blobs in tool results and
        ``resources/read`` responses. Set ``None`` to disable the library cap.

The ``LitestarMCP`` constructor also accepts a top-level ``prompts``
argument — a sequence of ``@mcp_prompt``-decorated callables — for
standalone prompt registration (see above).

Filters apply both to list responses and direct invocation. A filtered tool
or resource is omitted from ``tools/list``, ``resources/list``, or
``resources/templates/list`` and a direct ``tools/call`` / ``resources/read``
returns the same not-found response as an unknown name or URI. Filters narrow
the MCP exposure surface; use ``guards`` or auth middleware for access
control.

Tool-Call Callbacks
===================

Use ``before_tool_call`` and ``after_tool_call`` when you need audit,
metrics, or tracing hooks that are independent of route ownership layers.
Both callbacks receive the MCP tool name, a shallow copy of the submitted
arguments, and the synthesized :class:`litestar.Request` used for the
handler dispatch. ``after_tool_call`` also receives ``result``,
``exception``, and ``duration`` keyword-only values, and fires for
successes, guard failures, error responses, and unhandled exceptions.
Callback exceptions are logged and swallowed so observability code does
not alter tool-call behavior.

.. literalinclude:: /examples/snippets/configuration_tool_callbacks.py
    :language: python
    :caption: ``docs/examples/snippets/configuration_tool_callbacks.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

List Pagination
===============

``tools/list``, ``resources/list``, ``resources/templates/list``, and
``prompts/list`` are paginated per the MCP spec's opaque-cursor model.
Each accepts an optional ``cursor`` parameter and returns ``nextCursor``
when another page is available; treat ``nextCursor`` as an opaque token
and pass it back as ``params.cursor`` until the response omits it. An
invalid cursor returns ``INVALID_PARAMS`` (``-32602``).

The MCP spec lets the **server** pick the page size — clients cannot
request a ``limit`` on these methods. Set it with the
``MCPConfig.list_page_size`` option (e.g. ``MCPConfig(list_page_size=25)``);
it defaults to ``100`` and must be a positive integer.

These methods enumerate the *catalog* of registered primitives, which is
built once at startup; the cursor is a base64-encoded offset into that
stable list. Implement application-data pagination inside an individual
tool. The Tasks extension intentionally has no list method.

Environment Overrides
=====================

:class:`~litestar_mcp.MCPConfig` is a plain dataclass, so the ordinary
Litestar pattern applies: read the environment before constructing it and
pass the resolved values through. For example, to keep ``base_path`` and
``name`` configurable at deploy time:

.. code-block:: bash

    export MCP_BASE_PATH=/api/mcp
    export MCP_SERVER_NAME="My MCP Server"

Then build ``MCPConfig`` using ``os.getenv`` for each option - the shape
is identical to the :ref:`Custom Configuration <usage/configuration:Custom
Configuration>` snippet above, just with environment lookups replacing
literal values.
