==============
Marking Routes
==============

Routes are exposed to MCP by attaching metadata to their ``opt`` dictionary.
The plugin scans every handler at startup and treats any route tagged with
``mcp_tool`` or ``mcp_resource`` as part of the MCP surface.

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

Prompts are reusable message templates. Tag a handler with
``mcp_prompt="<prompt_name>"`` to publish it via ``prompts/list`` and
``prompts/get``. The return value is normalised to MCP ``PromptMessage``
content, and declared signature parameters become prompt ``arguments``.

.. literalinclude:: /examples/snippets/marking_prompts.py
    :language: python
    :caption: ``docs/examples/snippets/marking_prompts.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Four optional ``opt`` kwargs refine a marked prompt (each overrides the value
otherwise derived from the handler):

.. list-table::
    :widths: 30 70
    :header-rows: 1

    * - Kwarg
      - Effect
    * - ``mcp_prompt``
      - Required. The prompt name exposed to clients.
    * - ``mcp_prompt_title``
      - Human-readable display title.
    * - ``mcp_prompt_description``
      - Description shown in ``prompts/list``.
    * - ``mcp_prompt_arguments``
      - Explicit argument list, overriding signature introspection.
    * - ``mcp_prompt_icons``
      - Icon metadata for clients that render prompt pickers.

Standalone callables that are not route handlers can instead be decorated with
:func:`~litestar_mcp.mcp_prompt` and passed to ``LitestarMCP(prompts=[...])`` —
see :ref:`usage/configuration:Standalone Prompts`.

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
ships :func:`~litestar_mcp.decorators.mcp_tool` and
:func:`~litestar_mcp.decorators.mcp_resource`. Both carry the same metadata
as the ``opt`` kwargs and are interchangeable at discovery time.

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
