=========
Changelog
=========

All notable Litestar MCP changes are summarized here. Entries are grouped by
release and focus on user-visible behavior, public API changes, compatibility
notes, and important protocol fixes.

Recent Updates
==============

.. changelog:: 0.13.2

    .. change:: resolve tool wire names from ``Parameter(name=...)``
        :type: bugfix
        :pr: 93
        :issue: 92

        A parameter whose wire alias came from ``QueryParameter(name=...)`` or
        ``ParameterKwarg(name=...)`` was advertised and dispatched under its
        python name, so Litestar never read it and the handler ran as if the
        argument had never been sent. This made every ``advanced-alchemy``
        filter provider silently inert over MCP. Wire names now resolve from
        ``.name``, and header/cookie parameters are excluded from query
        wire-name resolution.

        Advertised names change for affected parameters (for example
        ``category_name_in`` becomes ``categoryNameIn``). For compatibility,
        the python name is still accepted at dispatch and rewritten to the
        wire name, logging a warning; the wire name wins when both are sent.

    .. change:: correct the contributor clone URL
        :type: misc
        :pr: 91

        The README development instructions now clone the canonical
        ``cofin/litestar-mcp`` repository.

.. changelog:: 0.13.1
    :date: 2026-08-24

    .. change:: preserve native dispatch for controllers and request bodies
        :type: bugfix
        :pr: 94

        MCP tools declared on controllers now resolve path parameters without
        requiring route handlers to support weak references. Path metadata is
        cached per application so separate Litestar applications remain
        isolated.

        Omitted request bodies now use the handler's declared default, while
        explicit falsey JSON bodies such as ``null``, ``false``, ``0``, empty
        strings, empty lists, and empty objects are passed through unchanged.

.. changelog:: 0.13.0
    :date: 2026-08-19

    .. change:: configure MCP router options and discovery route ownership
        :type: feature
        :pr: 88

        Added ``MCPConfig.route_opt`` for mounted-router policies and separate
        controls for registering the OAuth protected-resource metadata and
        agent-card discovery routes. Applications can now let another plugin
        own the RFC 9728 root path without a duplicate-route startup failure.

.. changelog:: 0.12.0
    :date: 2026-07-29

    .. change:: Stateless MCP 2026-07-28
        :type: breaking

        MCP is now POST-only and request-scoped. The initialize handshake,
        sessions, ping, GET/DELETE transport handlers, replay, and the
        experimental MCP server manifest were removed. Every request carries
        protocol, client, method, name, and custom-header metadata; use
        ``server/discover`` for capabilities.

    .. change:: Tasks, subscriptions, and MRTR
        :type: feature

        Added filtered ``subscriptions/listen`` streams, the opt-in
        ``io.modelcontextprotocol/tasks`` extension, durable Litestar Store
        task records, and typed multi-round-trip ``input_required`` results
        for tools, resources, and prompts.

    .. change:: Concurrent stdio bridge
        :type: feature

        The bridge now forwards independent request-scoped POST streams
        concurrently, multiplexes subscription responses, maps cancellation
        to stream closure, and lazily maps annotated tool parameters to MCP
        headers.

.. changelog:: 0.11.1

    .. change:: hide MCP plugin discovery routes from OpenAPI by default
        :type: bugfix

        ``MCPConfig.include_in_schema=False`` now hides the plugin-owned
        ``/.well-known/oauth-protected-resource``,
        ``/.well-known/agent-card.json``, and
        ``/.well-known/mcp-server.json`` routes from generated OpenAPI
        schemas, matching the existing default behavior for the ``/mcp``
        transport route. Marked application routes remain visible in OpenAPI
        unless the application hides them separately, and
        ``include_in_schema=True`` opts all MCP plugin routes back in.

.. changelog:: 0.11.0

    .. change:: support binary resources and explicit tool content blocks
        :type: feature

        Adds ``MCPResourceLink``, ``MCPBlobResource``, and ``MCPToolResult``
        helpers for protocol-shaped tool results. Marked resources can now
        advertise MIME metadata with ``mcp_resource_mime_type`` or
        ``mcp_resource(..., mime_type=...)`` and ``resources/read`` returns
        binary handler responses as base64 ``blob`` contents. Embedded blobs
        are capped by ``MCPConfig.max_blob_bytes`` before encoding; set it to
        ``None`` to rely on deployment or client limits instead.

.. changelog:: 0.10.0

    .. change:: add a Litestar CLI stdio bridge for Streamable HTTP
        :type: feature
        :pr: 79

        Adds ``litestar --app my_app:app mcp bridge`` and the
        ``litestar-mcp[bridge]`` extra for stdio-only MCP clients that need
        to reach a running Litestar MCP app. The command is registered by
        the ``LitestarMCP`` CLI plugin, defaults to the loaded app's
        ``MCPConfig.base_path``, and supports base URL overrides, explicit
        endpoints, discovery from ``/.well-known/mcp-server.json``, static
        headers, token providers, stream errors, and session cleanup.
        It shields stdio output from accidental application ``print`` calls,
        reports unreachable endpoints with a bridge-owned error message, and
        caps each stdin JSON-RPC message at 16 MiB by default, configurable
        with ``--max-message-size``.
        The bridge is implemented directly on ``httpx`` and optional
        ``httpx-sse`` and does not depend on the official ``mcp`` Python SDK
        or its server-side transport dependencies.

    .. change:: support authenticated stdio principals
        :type: feature
        :pr: 76
        :issue: 74

        Adds public ``MCPStdioContext`` support for
        ``MCP.run(transport="stdio", stdio_context=...)``. Standalone stdio
        dispatch now seeds synthetic Litestar scopes with ``user``, ``auth``,
        copied ``session``, copied ``state``, and a resolved ``owner_id`` so
        tools, resources, prompts, guards, and task execution can run on
        behalf of a process-local principal.

    .. change:: allow custom auth headers and token prefixes
        :type: feature
        :pr: 77
        :issue: 75

        ``MCPAuthBackend`` now accepts ``header_name`` and ``token_prefix``
        options. Existing ``Authorization: Bearer`` deployments keep the
        default behavior, while identity proxy deployments such as Google
        Cloud IAP and AWS ALB OIDC can validate raw or custom-prefixed JWTs
        from their provider-specific headers.

    .. change:: honor custom base paths in standalone internal routes
        :type: bugfix
        :pr: 79

        Standalone ``MCP.tool()``, ``MCP.resource()``, and ``MCP.prompt()``
        internal dispatch routes now follow ``MCPConfig.base_path`` instead
        of hard-coding ``/mcp/internal/...``.

    .. change:: return the correct SSE content type for GET streams
        :type: bugfix
        :pr: 79

        ``GET /mcp`` now advertises ``text/event-stream`` so strict SSE
        clients accept the Streamable HTTP event stream.

    .. change:: reduce routine lifecycle log noise
        :type: bugfix
        :pr: 76
        :issue: 73

        Routine startup, registry callback, and router-invalidation lifecycle
        messages are now logged at debug level instead of warning level.

    .. change:: align reference and security documentation
        :type: misc
        :pr: 78 79

        Adds reference coverage for ``MCP``, ``MCPStdioContext``,
        auth helpers, tool-call callback protocols, and the bridge API. Also
        adds security guidance for domain authorization, bridge identity
        boundaries, and safe file/path arguments.


.. changelog:: 0.9.0
    :date: 2026-06-29

    .. change:: add a standalone MCP application surface
        :type: feature
        :pr: 70
        :issue: 71 72

        Adds ``MCP(...)`` for applications whose primary surface is an MCP
        server. Standalone apps can register ``@mcp.tool``,
        ``@mcp.resource``, and ``@mcp.prompt`` callables, lazily expose
        ``mcp.app``, pass normal Litestar app and route-handler options, run
        SSE through the Litestar CLI, and run line-delimited stdio JSON-RPC
        while manually driving ASGI lifespan.

    .. change:: tighten standalone transport internals
        :type: misc
        :breaking:
        :pr: 70

        Internal transport callers now pass a ``RequestContext`` to
        ``JSONRPCRouter.dispatch()``. ``litestar_mcp.routes.build_jsonrpc_router``
        is no longer exported, and handler-signature helpers moved to
        ``litestar_mcp.utils.handler_signature``. Applications should prefer
        the public ``MCP`` and ``LitestarMCP`` entry points instead of private
        router construction.

    .. change:: clean up runtime, docs, and optional dependency internals
        :type: misc
        :pr: 70

        Refreshes examples, snippet coverage, optional Dishka dependency-key
        handling, router caching/session plumbing, framework-native
        serialization paths, release tooling, and Python 3.14 CI coverage.


.. changelog:: 0.8.0
    :date: 2026-06-24

    .. change:: add tool-call observability callbacks
        :type: feature
        :pr: 69
        :issue: 68

        Adds ``MCPConfig.before_tool_call`` and
        ``MCPConfig.after_tool_call`` hooks around ``tools/call`` dispatch for
        audit, metrics, and tracing use cases. The after hook receives either
        the result or exception plus elapsed dispatch duration, and hook
        failures are logged without changing tool-call behavior.

    .. change:: exclude Dishka-resolved provider params from tool inputs
        :type: bugfix
        :pr: 69
        :issue: 67

        Litestar ``Provide(...)`` factory parameters whose annotated type can
        be resolved from ``app.state.dishka_container`` are no longer emitted
        as MCP tool arguments. Ordinary provider-declared inputs, such as
        pagination and filter values, still appear in schemas and dispatch.


.. changelog:: 0.7.2
    :date: 2026-06-11

    .. change:: include provider-declared query parameters in MCP schemas
        :type: bugfix
        :pr: 65
        :issue: 64

        Query parameters declared on Litestar dependency providers now appear in
        MCP tool input schemas and are forwarded during tool execution, so
        provider-backed pagination and aliases are discoverable and callable by
        MCP clients.


.. changelog:: 0.7.1
    :date: 2026-06-09

    .. change:: fix published project URLs
        :type: bugfix
        :pr: 60

        Corrects package metadata links so the project URL points at the GitHub
        repository and the documentation URL points at the published docs site.

    .. change:: return 202 for accepted Streamable HTTP notifications
        :type: bugfix
        :issue: 61

        Accepted JSON-RPC notifications over MCP Streamable HTTP now return
        ``202 Accepted`` with an empty body, matching the MCP transport
        requirement for accepted JSON-RPC notifications and responses sent via
        POST.

    .. change:: apply filters to direct tool and resource invocation
        :type: bugfix
        :issue: 62

        ``include_*`` and ``exclude_*`` filters now gate direct ``tools/call``
        and ``resources/read`` invocation in addition to list responses. A
        filtered tool, resource, or resource template is treated like an
        unknown name or URI. Filters narrow the MCP exposure surface; Litestar
        guards and auth middleware remain the access-control boundary.


.. changelog:: 0.7.0
    :date: 2026-06-07

    .. change:: paginate MCP list methods
        :type: feature
        :pr: 58
        :issue: 47

        Adds opaque cursor pagination for MCP list methods so clients can page
        through tools, resources, resource templates, and prompts using
        ``nextCursor``.

    .. change:: converge handler signature introspection
        :type: bugfix
        :pr: 58
        :issue: 49

        Uses the same handler-signature introspection path for schema
        generation and execution-time argument handling, reducing drift between
        advertised input schemas and accepted call arguments.

    .. change:: document the MCP primitive error contract
        :type: bugfix
        :pr: 58
        :issue: 48

        Locks and documents the primitive-level error contract for tools,
        resources, and prompts. Handler HTTP status is preserved in error data
        where relevant instead of minting non-standard JSON-RPC codes.

    .. change:: document MCP Prompts end-to-end
        :type: misc
        :pr: 58
        :issue: 56

        Adds Prompts coverage across the usage guide, API reference, README,
        and task-manager example.


.. changelog:: 0.6.0
    :date: 2026-06-04

    .. change:: add MCP Prompts support
        :type: feature
        :pr: 46

        Adds MCP Prompts support, including prompt discovery and retrieval via
        ``prompts/list`` and ``prompts/get``.

    .. change:: update for Litestar 3 deprecations
        :type: misc
        :pr: 54

        Updates runtime, docs, and tests for Litestar 3 deprecation paths.

    .. change:: unwrap Annotated parameters in input schemas
        :type: bugfix
        :pr: 53
        :issue: 52

        Unwraps ``Annotated[T, Parameter(...)]`` declarations when generating
        MCP tool input schemas so Litestar parameter metadata does not hide the
        underlying value type.


.. changelog:: 0.5.1
    :date: 2026-04-19

    .. change:: restore full Litestar execution parity
        :type: bugfix
        :pr: 44
        :issue: 41 42 43

        Runs MCP tool execution through the full Litestar request lifecycle for
        hooks, renamed fields, and path-parameter coercion.


.. changelog:: 0.5.0
    :date: 2026-04-19

    .. change:: add consumer-readiness features
        :type: feature
        :pr: 40

        Adds structured tool/resource descriptions, resource templates,
        ``resources/templates/list``, ``completion/complete``, injectable JWKS
        cache support, and well-known discovery documents.

    .. change:: refresh docs and examples for consumer usage
        :type: misc
        :pr: 40

        Raises the supported Python floor to 3.10, renames bundled examples to
        ``hello_world`` and ``task_manager``, adds example tests and snippet
        markers, and updates docs to prefer Litestar route kwargs such as
        ``mcp_tool="name"``.

    .. change:: remove scope enforcement and auth extra
        :type: misc
        :breaking:
        :pr: 40

        Removes inline scope enforcement so scopes are discovery metadata and
        Litestar guards are the access-control surface. The legacy auth extra
        was removed and auth dependencies now install with the core package.


.. changelog:: 0.4.0
    :date: 2026-04-16

    .. change:: switch to Streamable HTTP
        :type: feature
        :pr: 32

        Replaces the legacy REST endpoint surface with MCP Streamable HTTP,
        using ``GET /mcp`` for SSE and ``POST /mcp`` for JSON-RPC requests.

    .. change:: add database integration test matrix
        :type: misc
        :pr: 34

        Adds database-backed integration coverage for Advanced Alchemy,
        SQLSpec, Dishka, and auth-mode combinations.


.. changelog:: 0.3.0
    :date: 2026-03-22

    .. change:: align with MCP JSON-RPC, transport, and auth specs
        :type: feature
        :pr: 13

        Adds MCP spec compliance for JSON-RPC 2.0, Streamable HTTP, and OAuth
        auth bridging.


.. changelog:: 0.2.2
    :date: 2025-09-30

    .. change:: remove duplicate CLI pass_context decorator
        :type: bugfix
        :pr: 6

        Removes a duplicate ``pass_context`` decorator from CLI commands.


.. changelog:: 0.2.1
    :date: 2025-09-28

    .. change:: add Litestar CLI plugin integration
        :type: feature
        :pr: 5

        Implements Litestar CLI plugin integration.


.. changelog:: 0.2.0
    :date: 2025-09-27

    .. change:: add CLI interface
        :type: feature
        :pr: 4

        Adds the initial command-line interface.


.. changelog:: 0.1.0
    :date: 2025-09-06

    .. change:: initial release
        :type: feature

        Adds the initial Litestar MCP plugin with route marking via
        ``mcp_tool`` and ``mcp_resource`` kwargs, automatic route discovery,
        REST-based MCP endpoints, OpenAPI schema exposure, ``MCPConfig``, and
        support for tools and resources.
