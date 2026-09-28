=====
Types
=====

This module contains type definitions for the Litestar MCP Plugin.

.. currentmodule:: litestar_mcp.mcp.config

MCPConfig
---------

:class:`MCPConfig` is documented in :doc:`config`.

MCPTaskConfig
-------------

.. autoclass:: MCPTaskConfig
   :members:
   :show-inheritance:

MCPSkillsConfig
----------------

.. autoclass:: MCPSkillsConfig
   :members:
   :show-inheritance:

.. currentmodule:: litestar_mcp.mcp.app

MCPStdioContext
---------------

Runtime identity for standalone stdio transports. Its fields seed the
synthesized dispatch scope so handlers, guards, and task execution read the
usual ``request.user`` / ``request.scope["auth"]`` / session / state. See
:doc:`/usage/standalone_app` for the stdio walkthrough.

.. autoclass:: MCPStdioContext
   :members:
   :show-inheritance:

.. currentmodule:: litestar_mcp.mcp.content

MCPResourceLink
---------------

.. autoclass:: MCPResourceLink
   :members:
   :show-inheritance:

MCPBlobResource
---------------

.. autoclass:: MCPBlobResource
   :members:
   :show-inheritance:

MCPToolResult
-------------

.. autoclass:: MCPToolResult
   :members:
   :show-inheritance:

.. currentmodule:: litestar_mcp.mcp.registry

PromptRegistration
------------------

See also :class:`~litestar_mcp.mcp.config.MCPOptKeys` (documented under
:doc:`config`) for the opt-key field names that drive handler-based
prompt discovery.

.. autoclass:: PromptRegistration
   :members:
   :show-inheritance:
