========
Examples
========

Two runnable example applications ship with Litestar MCP under
``docs/examples/``:

- ``docs/examples/basic/`` — the smallest possible MCP integration: one
  tool (``add``), one resource (``pi``), and a plain non-MCP route, in
  under 50 lines.
- ``docs/examples/advanced/`` — a SQLite-backed task management API
  modeled on advanced-alchemy's upstream Litestar service example, with
  per-request dependency injection, pagination, search, and collection
  filters wired through to MCP tool calls.

The authoritative hands-on guide — setup, running instructions (HTTP +
offline CLI), curl examples, feature matrix, and troubleshooting — lives
in the examples README, rendered as the subpage below:

.. toctree::
    :maxdepth: 2

    examples/README

Example Use Cases
=================

**For AI Models:**

The MCP endpoints enable AI models to:

1. **Explore your API**: Discover available routes and their parameters
2. **Validate requests**: Check if endpoints exist before making requests
3. **Access data**: Retrieve application-specific information
4. **Execute tools**: Perform custom operations you define

**For Development:**

- **API Documentation**: MCP provides machine-readable API metadata
- **Testing**: Validate your application structure programmatically
- **Debugging**: Inspect application state and configuration
- **Integration**: Enable AI-powered development tools

Next Steps
==========

- Create your own MCP-enabled routes based on these examples
- Explore the :doc:`usage/marking-routes` guide
- Check the :doc:`reference/index` for API details
