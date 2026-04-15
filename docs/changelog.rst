=========
Changelog
=========

All notable changes to this project will be documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_,
and this project adheres to `Semantic Versioning <https://semver.org/spec/v2.0.0.html>`_.

Unreleased
----------

Changed
~~~~~~~

- Switched the transport surface to MCP Streamable HTTP with ``GET /mcp`` for SSE and ``POST /mcp`` for JSON-RPC requests.
- Added well-known discovery documents for MCP clients and agent metadata.
- Raised the supported Python floor to 3.10.
- Renamed the bundled example directories from ``docs/examples/basic/`` and
  ``docs/examples/advanced/`` to ``docs/examples/hello_world/`` and
  ``docs/examples/task_manager/``. Each example now ships with a sibling
  pytest module and ``# start-example`` / ``# end-example`` marker blocks so
  the usage guide can pull snippets via ``.. literalinclude:: :dedent: 4``.
  No compatibility shim is provided - update any external references to use
  the new paths.

Removed
~~~~~~~

- Removed the legacy REST MCP endpoints and session-oriented transport surface.

v0.1.0 (2025-01-04)
-------------------

Added
~~~~~

- Initial implementation of the Litestar MCP Plugin
- Route marking using ``mcp_tool`` and ``mcp_resource`` kwargs
- Automatic discovery of marked routes via opt dictionary scanning
- REST-based MCP endpoints (``/mcp/``, ``/mcp/tools``, ``/mcp/resources``)
- OpenAPI schema integration and exposure as MCP resource
- Minimal configuration with ``MCPConfig`` class
- Support for both tools (executable functions) and resources (read-only data)
