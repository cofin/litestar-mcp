=================
Migrating to 0.14
=================

Version 0.14 removes the non-standard MCP agent card. Use ``server/discover``
for MCP capability discovery. Applications needing A2A should install
``litestar-mcp[a2a]`` and configure ``LitestarA2A`` with the official SDK's
``AgentCard`` and ``RequestHandler``.

The unreleased proprietary A2A agents, decorators, task models, CLI, MCP
auto-export, ``litestar_mcp.core``, and ``litestar_mcp.mcp`` layouts are not
part of the public API and have no compatibility aliases.
