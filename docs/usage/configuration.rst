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

Task Lifecycle
==============

Enable the experimental in-memory task endpoints by passing an
:class:`~litestar_mcp.config.MCPTaskConfig`. Tasks let MCP clients submit
long-running work and poll for completion.

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
      - Enable experimental in-memory MCP task support.

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
