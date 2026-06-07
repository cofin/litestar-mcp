=======
Prompts
=======

Prompts are templated instructions for a model. Unlike tools (which
execute) and resources (which return data), a prompt returns a list of
messages the client feeds back into an LLM. The plugin supports two
registration paths:

* **Standalone prompt functions** — plain (async) callables decorated
  with :func:`~litestar_mcp.mcp_prompt` and passed to
  ``LitestarMCP(prompts=[...])``. These are not routed under HTTP; they
  are reachable only through ``prompts/get``.
* **Handler-based prompts** — Litestar route handlers tagged with
  ``mcp_prompt="<name>"``. The handler stays a normal HTTP endpoint *and*
  is published through ``prompts/get``, with full access to DI, guards,
  and signature validation.

The task-manager demo registers a single standalone prompt that
summarises the current task list:

.. literalinclude:: /examples/task_manager/main.py
    :language: python
    :caption: ``docs/examples/task_manager/main.py`` - ``register_prompts``
    :pyobject: register_prompts

See :doc:`marking_routes` for the handler-based opt-key form and the full
``mcp_prompt*`` opt-key reference.

Return Value Normalisation
==========================

A prompt's return value is normalised to a list of MCP ``PromptMessage``
dicts before it goes on the wire:

* ``str`` → single user-role text message.
* ``dict`` with a ``role`` key and a recognised content block
  (``text`` / ``image`` / ``audio`` / ``resource_link`` / ``resource``)
  is wrapped in a list and used as-is.
* ``list`` items follow the same dict rules.
* Anything else is coerced to ``str(result)`` with a warning log.

In practice this means the simplest possible prompt body — returning a
single string — Just Works, and richer multi-message replies use the
dict / list form. The advertised ``arguments`` list is derived from the
function signature (or the handler's ``parsed_fn_signature`` for the opt-key
form), enriched with Google-style docstring descriptions when present.

Capability Gating
=================

The ``prompts`` capability is only advertised — both in ``initialize``'s
capability response and in ``/.well-known/mcp-server.json`` — when at
least one visible prompt is registered. This matches the MCP spec's
recommendation that servers only declare capabilities for primitives
they actually expose. The same per-tag and per-operation include/exclude
filters that gate tools and resources also gate prompt visibility.

JSON-RPC Round-Trip
===================

After ``initialize``, clients drive prompts via ``prompts/list`` and
``prompts/get``:

.. code-block:: bash

    # List every prompt the server publishes
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":1,"method":"prompts/list","params":{}}'

    # Render a specific prompt (task-manager demo)
    curl -sS -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":2,"method":"prompts/get",
           "params":{"name":"summarize_tasks",
                     "arguments":{"style":"detailed"}}}'

A missing required argument surfaces as JSON-RPC ``INVALID_PARAMS``
(``-32602``); an unknown prompt name and an unknown argument name do the
same — the executor validates the argument set against the handler's
``parsed_fn_signature`` before dispatch. If a handler-based prompt fails
*during* execution, the error maps to ``INTERNAL_ERROR`` (``-32603``)
with the handler's HTTP status preserved in ``data.statusCode`` — the
same contract resources use (see :doc:`resources`). The JSON-RPC ``code``
reflects the primitive-level error class, not the raw HTTP status.
