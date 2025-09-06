=========
Changelog
=========

All notable changes to this project will be documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_,
and this project adheres to `Semantic Versioning <https://semver.org/spec/v2.0.0.html>`_.

Unreleased
----------

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
