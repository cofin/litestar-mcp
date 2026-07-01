=====
Types
=====

This module contains type definitions for the Litestar MCP Plugin.

.. currentmodule:: litestar_mcp.config

MCPConfig
---------

.. autoclass:: MCPConfig
   :members:
   :show-inheritance:

MCPTaskConfig
-------------

.. autoclass:: MCPTaskConfig
   :members:
   :show-inheritance:

.. currentmodule:: litestar_mcp.app

MCPStdioContext
---------------

Runtime identity for standalone stdio transports. Its fields seed the
synthesized dispatch scope so handlers, guards, and task execution read the
usual ``request.user`` / ``request.scope["auth"]`` / session / state. See
:doc:`/usage/standalone_app` for the stdio walkthrough.

.. autoclass:: MCPStdioContext
   :members:
   :show-inheritance:

.. currentmodule:: litestar_mcp.registry

PromptRegistration
------------------

See also :class:`~litestar_mcp.config.MCPOptKeys` (documented under
:doc:`config`) for the opt-key field names that drive handler-based
prompt discovery.

.. autoclass:: PromptRegistration
   :members:
   :show-inheritance:

.. currentmodule:: litestar_mcp.auth

MCPAuthConfig
-------------

.. autoclass:: MCPAuthConfig
   :members:
   :show-inheritance:

OIDCProviderConfig
------------------

.. autoclass:: OIDCProviderConfig
   :members:
   :show-inheritance:
