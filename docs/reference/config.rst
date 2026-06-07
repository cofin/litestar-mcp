==========
Config API
==========

This module contains the configuration classes for the Litestar MCP Plugin.

.. currentmodule:: litestar_mcp.config

MCPConfig
---------

.. autoclass:: MCPConfig
   :members:
   :show-inheritance:

MCPOptKeys
----------

The opt-key names used to mark Litestar route handlers as MCP tools,
resources, or prompts. Field defaults match the documented kwargs
(``mcp_tool``, ``mcp_resource``, ``mcp_prompt``, ``mcp_prompt_description``,
``mcp_prompt_title``, ``mcp_prompt_arguments``, ``mcp_prompt_icons``, …);
override the dataclass to remap a project to non-default opt keys.

.. autoclass:: MCPOptKeys
   :members:
   :show-inheritance:
