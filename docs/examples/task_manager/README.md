---
orphan: true
---

# Task Manager Litestar MCP Example

A broader example of integrating the Litestar MCP Plugin — a small task-management API that exposes CRUD operations as MCP tools and a schema + API-info pair as MCP resources.

This directory follows the project's hybrid example convention: ``main.py`` is a runnable script, and ``test_task_manager.py`` is its pytest companion. The resource-registration, tool-registration, and plugin-wiring blocks are each wrapped in ``# start-example`` / ``# end-example`` markers so the usage guide can include them with ``:dedent: 4``.

## What This Example Demonstrates

- Registering MCP tools via ``mcp_tool=`` on route handlers.
- Registering MCP resources via ``mcp_resource=`` on route handlers.
- Constructing the Litestar app through a ``build_app()`` factory so tests can inject a fresh in-memory task store per run.

## Running the Example

```bash
uv run python docs/examples/task_manager/main.py
```

The server starts on ``http://127.0.0.1:8000`` with the MCP transport surface at ``/mcp`` and OpenAPI docs at ``/schema/swagger``.

## Testing

```bash
uv run pytest docs/examples/task_manager
```

Each test builds a fresh app through ``build_app(tasks=…)`` so the task store is isolated between cases.
