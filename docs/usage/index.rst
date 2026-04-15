===========
Usage Guide
===========

The Litestar MCP plugin follows a simple mental model: **mark routes → the
plugin discovers them → clients interact via the MCP transport**. These
pages cover each piece of that pipeline in isolation, with runnable
examples drawn from :mod:`docs.examples`.

.. grid:: 1 1 2 3
    :gutter: 2
    :padding: 0

    .. grid-item-card:: Configuration
        :link: configuration
        :link-type: doc

        Configure :class:`~litestar_mcp.MCPConfig`, task lifecycle, and
        environment overrides.

    .. grid-item-card:: Marking Routes
        :link: marking_routes
        :link-type: doc

        Expose handlers with ``mcp_tool`` / ``mcp_resource`` kwargs or the
        dedicated decorator.

    .. grid-item-card:: Tools & Resources
        :link: tools_and_resources
        :link-type: doc

        How marked routes are registered, executed, and returned to MCP
        clients.

    .. grid-item-card:: Discovery
        :link: discovery
        :link-type: doc

        The ``/.well-known/*`` manifests the plugin publishes automatically.

    .. grid-item-card:: Authentication
        :link: auth
        :link-type: doc

        Bearer-token validation, OIDC providers, and mapping claims to
        users.

    .. grid-item-card:: Framework Integration
        :link: framework_integration
        :link-type: doc

        Plugin ordering, guards, OpenAPI, and custom base paths.

.. toctree::
    :hidden:
    :maxdepth: 1

    configuration
    marking_routes
    tools_and_resources
    discovery
    auth
    framework_integration
