==============
Marking Routes
==============

Routes are exposed to MCP by attaching metadata to their ``opt`` dictionary.
The plugin scans every handler at startup and treats any route tagged with
``mcp_tool``, ``mcp_resource``, or ``mcp_prompt`` as part of the MCP surface.
Standalone prompt callables that are *not* routed under HTTP are registered
separately via ``LitestarMCP(prompts=[...])`` — see
:ref:`Prompt Marker <usage/marking_routes:Prompt Marker>` below.

Tool Marker
===========

Tools are executable operations - anything that takes arguments and returns
structured output. Tag a handler with ``mcp_tool="<tool_name>"`` and the
plugin publishes it via ``tools/list`` and ``tools/call``.

.. literalinclude:: /examples/snippets/marking_tools.py
    :language: python
    :caption: ``docs/examples/snippets/marking_tools.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Resource Marker
===============

Resources are read-only payloads such as schemas, capability summaries, or
cached projections. Tag a handler with ``mcp_resource="<resource_name>"`` to
expose it via ``resources/list`` and ``resources/read``.

.. literalinclude:: /examples/snippets/marking_resources.py
    :language: python
    :caption: ``docs/examples/snippets/marking_resources.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Prompt Marker
=============

Prompts are templated instructions for a model. The plugin recognises
two registration paths.

**Handler-based prompts.** Tag a Litestar route handler with
``mcp_prompt="<prompt_name>"``. The handler stays a normal HTTP endpoint
and is also reachable through ``prompts/get``. Override the description,
title, argument list, or icons through dedicated opt keys:

.. list-table::
    :widths: 30 70
    :header-rows: 1

    * - Opt key
      - Effect
    * - ``mcp_prompt``
      - Required. The prompt name used in ``prompts/get``.
    * - ``mcp_prompt_description``
      - LLM-facing description (falls back to the handler's docstring).
    * - ``mcp_prompt_title``
      - Optional human-readable title for UI clients.
    * - ``mcp_prompt_arguments``
      - Explicit argument list (a ``list[dict[str, Any]]``). When omitted,
        the plugin introspects the handler's ``signature_model``, filters
        out DI dependencies and framework-injected parameters
        (``request``, ``headers``, ``state``, …), and enriches each entry
        with Google-style docstring descriptions.
    * - ``mcp_prompt_icons``
      - Optional list of MCP icon objects (``src``, ``mimeType``, ``sizes``).

**Standalone prompt functions.** Decorate a plain callable with
:func:`~litestar_mcp.mcp_prompt` and pass it to
``LitestarMCP(prompts=[...])``. These never appear in the HTTP route
table — they are MCP-only.

.. literalinclude:: /examples/snippets/marking_prompts.py
    :language: python
    :caption: ``docs/examples/snippets/marking_prompts.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Dependency Injection
====================

Marked routes participate in Litestar's dependency injection system exactly
like any other handler. Provide dependencies via ``dependencies={...}`` and
declare them in the signature; the plugin resolves them for each ``tools/call``.

.. literalinclude:: /examples/snippets/marking_dependencies.py
    :language: python
    :caption: ``docs/examples/snippets/marking_dependencies.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Decorator Variant
=================

If you prefer a dedicated decorator over the kwargs form, ``litestar_mcp``
ships :func:`~litestar_mcp.mcp_tool`, :func:`~litestar_mcp.mcp_resource`,
and :func:`~litestar_mcp.mcp_prompt`. The tool and resource decorators
carry the same metadata as the ``opt`` kwargs and are interchangeable at
discovery time on a route handler.

``mcp_prompt`` is different: the decorator and the ``mcp_prompt_*`` opt
keys target *different registration paths*. Use the opt keys to expose a
Litestar route handler as a prompt. Use the decorator to mark a plain
callable that you hand to ``LitestarMCP(prompts=[...])``. The decorator
does not act as the route-handler marker.

.. tabs::

    .. tab:: Kwargs form

        Prefer this when you are already passing route options inline:

        .. literalinclude:: /examples/snippets/marking_tools.py
            :language: python
            :start-after: # start-example
            :end-before: # end-example
            :dedent:

    .. tab:: Decorator form

        Use the explicit decorator when composing marked routes across
        modules or when you want an import-time marker:

        .. literalinclude:: /examples/snippets/marking_decorator.py
            :language: python
            :caption: ``docs/examples/snippets/marking_decorator.py``
            :start-after: # start-example
            :end-before: # end-example
            :dedent:

.. note::

    Both forms end up at the same registry entry. Pick one per project for
    consistency; mixing is supported but harder to audit.
