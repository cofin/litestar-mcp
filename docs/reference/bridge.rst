==========
Bridge API
==========

The stdio bridge adapts local MCP stdio clients to a Litestar MCP Streamable
HTTP endpoint. Most users should run it through Litestar's app-bound CLI:
``litestar --app my_app:app mcp bridge``. The functions below are available
for tests and embedded launchers.

.. currentmodule:: litestar_mcp.bridge

run_stdio_streamable_http_bridge
--------------------------------

.. autofunction:: run_stdio_streamable_http_bridge

DEFAULT_MAX_STDIN_MESSAGE_SIZE
------------------------------

.. autodata:: DEFAULT_MAX_STDIN_MESSAGE_SIZE

run_bridge
----------

.. autofunction:: run_bridge

MissingDependencyError
----------------------

.. autoclass:: MissingDependencyError
   :members:
   :show-inheritance:
