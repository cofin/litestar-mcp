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
   dictionary and exposes them through MCP-compatible REST endpoints.
