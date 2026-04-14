==========
Plugin API
==========

This module contains the main plugin class for the Litestar MCP Plugin.

.. currentmodule:: litestar_mcp.plugin

LitestarMCP
-----------

.. autoclass:: LitestarMCP
   :members:
   :show-inheritance:

   The main plugin class that implements :class:`litestar.plugins.InitPluginProtocol`.
   It discovers routes marked with ``mcp_tool`` or ``mcp_resource`` in their ``opt``
   dictionary and exposes them through the MCP Streamable HTTP transport surface.

.. currentmodule:: litestar_mcp.registry

Registry
--------

.. autoclass:: Registry
   :members:
   :show-inheritance:

.. currentmodule:: litestar_mcp.sse

SSEManager
----------

.. autoclass:: SSEManager
   :members:
   :show-inheritance:

SSEMessage
----------

.. autoclass:: SSEMessage
   :members:
   :show-inheritance:
