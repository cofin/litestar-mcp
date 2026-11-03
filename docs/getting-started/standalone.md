---
title: Standalone MCP Application
---

# Standalone MCP Application (`MCP`)

The `MCP` class provides a declarative wrapper around `Litestar` and `LitestarMCP` for applications whose primary purpose is serving Model Context Protocol tools, resources, prompts, and Agent Skills.

---

## 1. Basic Setup and `@mcp.tool()`

Instantiate `MCP` with a server name and optional instructions, then decorate functions with `@mcp.tool()`. Function signatures and type annotations compile automatically into MCP tool JSON Schemas:

```python title="docs/examples/snippets/standalone_minimal.py"
--8<-- "docs/examples/snippets/standalone_minimal.py"
```

---

## 2. Exposing Resources and Prompts

Use `@mcp.resource()` to register read-only data URIs (or RFC 6570 URI templates) and `@mcp.prompt()` to register reusable prompt templates:

=== "Resources (@mcp.resource)"

    ```python title="docs/examples/snippets/standalone_resource.py"
    --8<-- "docs/examples/snippets/standalone_resource.py"
    ```

=== "Prompts (@mcp.prompt)"

    ```python title="docs/examples/snippets/standalone_prompt.py"
    --8<-- "docs/examples/snippets/standalone_prompt.py"
    ```

---

## 3. Dependency Injection and Route Options

Every `@mcp.tool()`, `@mcp.resource()`, and `@mcp.prompt()` decorator accepts standard Litestar route handler options (`dependencies`, `guards`, `tags`, `summary`, `response_headers`, `opt`, and `sync_to_thread`). Because `name` sets the MCP primitive identifier, pass `route_name` when you need a distinct Litestar route name:

```python title="docs/examples/snippets/standalone_dependencies.py"
--8<-- "docs/examples/snippets/standalone_dependencies.py"
```

---

## 4. Accessing the Underlying `Litestar` App and Plugins

`MCP.app` lazily constructs the underlying `Litestar` instance once all decorators have been registered. You can also forward any `Litestar` constructor arguments (`plugins`, `middleware`, `guards`, `stores`, `on_startup`, `on_shutdown`) directly to `MCP(...)`:

=== "Accessing mcp.app"

    ```python title="docs/examples/snippets/standalone_app.py"
    --8<-- "docs/examples/snippets/standalone_app.py"
    ```

=== "Forwarding Litestar Plugins"

    ```python title="docs/examples/snippets/standalone_custom.py"
    --8<-- "docs/examples/snippets/standalone_custom.py"
    ```

---

## 5. Running Over Streamable HTTP vs Stdio

### Streamable HTTP (`transport="streamable-http"`)

The default `mcp.run()` mode starts the Streamable HTTP endpoint (`/mcp`) through the Litestar CLI. Expose `app = mcp.app` at module top-level so the CLI and worker processes can import the ASGI application:

```python title="docs/examples/snippets/standalone_run.py"
--8<-- "docs/examples/snippets/standalone_run.py"
```

### Stdio Transport and `MCPStdioContext` (`transport="stdio"`)

Set `transport="stdio"` to run in-process over standard input/output for desktop hosts (such as Claude Desktop or Cursor). Stdio enters `Litestar.lifespan()` so database pools, stores, and Dishka containers initialize in the exact same order as HTTP deployments.

Because stdio has no HTTP header layer, resolve caller identity from the host environment and pass it via `MCPStdioContext`:

```python title="docs/examples/snippets/standalone_run_stdio.py"
--8<-- "docs/examples/snippets/standalone_run_stdio.py"
```

`MCPStdioContext` populates `request.user`, `request.auth`, `request.session`, and `request.state` on the synthetic ASGI scope for every stdio request:

| `MCPStdioContext` Field | Synthetic ASGI Scope Target | Purpose |
| :--- | :--- | :--- |
| `user` | `scope["user"]` (`request.user`) | Authenticated user object inspected by route guards and tools |
| `auth` | `scope["auth"]` (`request.auth`) | Authentication claims dictionary or token payload |
| `owner_id` | Task & session ownership key | Explicit owner identifier (falls back to `user:<subject>` derived from `auth["sub"]` or `user.id`) |
| `session` | `scope["session"]` (`request.session`) | Litestar application session dictionary |
| `state` | `scope["state"]` (`request.state`) | Per-request state dictionary merged into the synthetic scope |
