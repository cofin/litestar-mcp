=============
Configuration
=============

The Litestar MCP Plugin uses a minimal configuration approach. This guide covers how to configure the plugin for different use cases.

Basic Configuration
-------------------

The plugin can be added to your Litestar application with default settings:

.. code-block:: python

    from litestar import Litestar
    from litestar_mcp import LitestarMCP

    app = Litestar(
        route_handlers=[],
        plugins=[LitestarMCP()]
    )

Custom Configuration
--------------------

Use :class:`MCPConfig <litestar_mcp.config.MCPConfig>` to customize plugin behavior:

.. code-block:: python

    from litestar import Litestar
    from litestar_mcp import LitestarMCP, MCPConfig

    config = MCPConfig(
        base_path="/api/mcp",          # Custom API base path
        include_in_schema=True,        # Include in OpenAPI schema
        name="My MCP Server"           # Custom server name
    )

    app = Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(config)]
    )

Configuration Options
---------------------

.. list-table::
   :widths: 30 20 50
   :header-rows: 1

   * - Option
     - Default
     - Description
   * - ``base_path``
     - ``"/mcp"``
     - Base path for MCP API endpoints
   * - ``include_in_schema``
     - ``False``
     - Whether to include MCP routes in OpenAPI schema
   * - ``name``
     - ``None``
     - Server name override (uses OpenAPI title if not set)
   * - ``guards``
     - ``None``
     - Optional list of Litestar guards applied to MCP endpoints
   * - ``include_operations``
     - ``None``
     - Limit discovery to specific operation IDs (None means all)
   * - ``exclude_operations``
     - ``None``
     - Operation IDs to exclude from discovery
   * - ``include_tags``
     - ``None``
     - Limit discovery to handlers with matching tags
   * - ``exclude_tags``
     - ``None``
     - Exclude handlers that include any matching tag
   * - ``sse_heartbeat_interval``
     - ``30``
     - Heartbeat interval (seconds) for SSE connections
   * - ``sse_connection_timeout``
     - ``300``
     - Maximum SSE connection duration (seconds)
   * - ``sse_batch_size``
     - ``10``
     - Max events per batch when streaming over SSE
   * - ``sse_flush_interval``
     - ``1.0``
     - Force flush interval (seconds) for SSE batching

Environment Integration
-----------------------

The plugin integrates with Litestar's configuration system and can use environment variables through standard Litestar patterns:

.. code-block:: python

    import os
    from litestar import Litestar
    from litestar_mcp import LitestarMCP, MCPConfig

    config = MCPConfig(
        base_path=os.getenv("MCP_BASE_PATH", "/mcp"),
        name=os.getenv("MCP_SERVER_NAME")
    )

    app = Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(config)]
    )
