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
        name="My MCP Server",          # Custom server name
        include_tags=["public"],       # Only expose selected tags
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
     - Base path for the MCP Streamable HTTP endpoint
   * - ``include_in_schema``
     - ``False``
     - Whether to include MCP routes in OpenAPI schema
   * - ``name``
     - ``None``
     - Server name override (uses OpenAPI title if not set)
   * - ``guards``
     - ``None``
     - Litestar guards applied to the MCP router
   * - ``allowed_origins``
     - ``None``
     - Restrict accepted ``Origin`` header values
   * - ``include_operations`` / ``exclude_operations``
     - ``None``
     - Filter exposure by Litestar operation name
   * - ``include_tags`` / ``exclude_tags``
     - ``None``
     - Filter exposure by OpenAPI tags
   * - ``auth``
     - ``None``
     - Enable bearer-token validation and OAuth protected resource metadata
   * - ``tasks``
     - ``False``
     - Enable experimental in-memory MCP task support

Auth Configuration
------------------

Use :class:`MCPAuthConfig <litestar_mcp.auth.MCPAuthConfig>` when you want MCP endpoints
to enforce bearer-token validation and publish OAuth protected resource metadata.

.. code-block:: python

    from litestar import Litestar
    from litestar_mcp import LitestarMCP, MCPConfig
    from litestar_mcp.auth import MCPAuthConfig

    async def validate_token(token: str) -> dict[str, str] | None:
        if token == "dev-token":
            return {"sub": "demo-user"}
        return None

    config = MCPConfig(
        auth=MCPAuthConfig(
            issuer="https://auth.example.com",
            audience="https://api.example.com",
            token_validator=validate_token,
        )
    )

    app = Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(config)]
    )

Task Configuration
------------------

Use :class:`MCPTaskConfig <litestar_mcp.config.MCPTaskConfig>` to enable the
experimental in-memory task lifecycle endpoints.

.. code-block:: python

    from litestar import Litestar
    from litestar_mcp import LitestarMCP, MCPConfig, MCPTaskConfig

    config = MCPConfig(
        tasks=MCPTaskConfig(
            enabled=True,
            list_enabled=True,
            cancel_enabled=True,
            default_ttl=300_000,
            max_ttl=3_600_000,
            poll_interval=1_000,
        )
    )

    app = Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(config)]
    )

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
