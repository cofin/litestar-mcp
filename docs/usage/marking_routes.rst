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
