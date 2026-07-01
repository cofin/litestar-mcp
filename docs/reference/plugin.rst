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
   It discovers routes marked with ``mcp_tool``, ``mcp_resource``, or
   ``mcp_prompt`` in their ``opt`` dictionary and exposes them through the
   MCP Streamable HTTP transport surface. Standalone prompt callables
   decorated with :func:`~litestar_mcp.mcp_prompt` can be passed via the
   ``prompts`` constructor argument.

.. currentmodule:: litestar_mcp.app

MCP
---

.. autoclass:: MCP
   :members:
   :show-inheritance:

   The standalone application wrapper. Builds a Litestar app pre-configured
   with :class:`~litestar_mcp.LitestarMCP`, exposes ``@tool`` / ``@resource``
   / ``@prompt`` decorators, and can serve over Streamable HTTP or stdio via
   :meth:`MCP.run`. Pass a :class:`~litestar_mcp.MCPStdioContext` to
   ``run(transport="stdio", stdio_context=...)`` to seed the caller identity.

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
