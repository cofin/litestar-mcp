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

    .. grid-item-card:: Standalone Application
        :link: standalone_app
        :link-type: doc

        A class wrapping Litestar to register tools,
        resources, and prompts declaratively.

    .. grid-item-card:: Marking Routes
        :link: marking_routes
        :link-type: doc

        Expose handlers with ``mcp_tool`` / ``mcp_resource`` /
        ``mcp_prompt`` kwargs or the dedicated decorator.

    .. grid-item-card:: Prompts
        :link: prompts
        :link-type: doc

        Templated instructions exposed via ``prompts/list`` and
        ``prompts/get``, including standalone and handler-based forms.

    .. grid-item-card:: Resources
        :link: resources
        :link-type: doc

        Read-only payloads served via ``resources/list`` and
        ``resources/read``, with RFC 6570 URI template support.

    .. grid-item-card:: Tools
        :link: tools
        :link-type: doc

        Executable operations served via ``tools/list`` and
        ``tools/call``, validated through Litestar signature models.

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

    .. grid-item-card:: Reference Examples
        :link: reference_examples
        :link-type: doc

        The ``docs/examples/notes/`` family chooser: Advanced Alchemy
        vs SQLSpec, no-auth/JWT/Cloud Run/IAP, with ``uvx`` run-first
        guidance.

    .. grid-item-card:: Deployment
        :link: deployment
        :link-type: doc

        Sticky routing on ``Mcp-Session-Id``, shared session stores,
        and multi-replica notes for Cloud Run / GKE.

    .. grid-item-card:: ADK Integration
        :link: adk
        :link-type: doc

        Connect Google ADK clients to your remote Litestar MCP server.

.. toctree::
    :hidden:
    :maxdepth: 1

    configuration
    standalone_app
    marking_routes
    prompts
    resources
    tools
    discovery
    auth
    framework_integration
    reference_examples
    uvx_guide
    deployment
    adk
