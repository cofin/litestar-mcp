=========
Changelog
=========

All notable Litestar MCP changes are summarized here. Entries are grouped by
release and focus on user-visible behavior, public API changes, compatibility
notes, and important protocol fixes.

Recent Updates
==============

.. changelog:: 0.7.3
    :date: 2026-06-24

    .. change:: add tool-call observability callbacks
        :type: feature
        :issue: 68

        Adds ``MCPConfig.before_tool_call`` and
        ``MCPConfig.after_tool_call`` hooks around ``tools/call`` dispatch for
        audit, metrics, and tracing use cases. The after hook receives either
        the result or exception plus elapsed dispatch duration, and hook
        failures are logged without changing tool-call behavior.

    .. change:: exclude Dishka-resolved provider params from tool inputs
        :type: bugfix
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
