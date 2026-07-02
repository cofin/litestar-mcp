==========
Bridge API
==========

The stdio bridge adapts local MCP stdio clients to a remote Streamable HTTP
MCP endpoint. Most users should run it through the ``litestar-mcp bridge``
console command; the functions below are available for tests and embedded
launchers.

.. currentmodule:: litestar_mcp.bridge

run_stdio_streamable_http_bridge
--------------------------------

.. autofunction:: run_stdio_streamable_http_bridge

run_bridge
----------

.. autofunction:: run_bridge

bridge_command
--------------

.. py:data:: bridge_command

   Click command registered as ``litestar-mcp bridge``.

MissingDependencyError
----------------------

.. autoclass:: MissingDependencyError
   :members:
   :show-inheritance:
