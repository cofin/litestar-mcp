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
   :widths: 25 25 50
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
