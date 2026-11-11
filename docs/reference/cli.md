---
title: CLI Reference
---

# CLI Reference

When `LitestarMCP` is registered on a Litestar application, it automatically attaches the `mcp` command group to the `litestar` CLI (`LitestarMCP.on_cli_init`).

For transport architecture and configuration patterns, see [Stdio, HTTP Bridge & Tool Spec Export](../learn/stdio-and-bridge.md).

## Command Reference

--8<-- "tools/docs/generated/cli-reference.md"

## Usage Examples

### In-Process Stdio Server

Run your Litestar application as a local MCP server over `stdin`/`stdout` without binding a TCP port. The in-process `ASGIStreamingTransport` enters `app.lifespan()` and dispatches every JSON-RPC frame through the full Litestar middleware, guard, and dependency injection stack:

```bash
# Serve the application in-process over stdio
litestar --app my_app.main:app mcp stdio

# Inject a bearer token from an environment variable into every synthesized ASGI scope
litestar --app my_app.main:app mcp stdio --bearer-env MCP_API_TOKEN

# Fetch a short-lived token dynamically via a shell command and attach a custom tenant header
litestar --app my_app.main:app mcp stdio \
  --bearer-cmd "gcloud auth print-identity-token" \
  --header "X-Tenant-Id: acme-prod"
```

### Stdio-to-Streamable HTTP Proxy

Connect a stdio-only MCP desktop client or IDE to a remote or local Streamable HTTP deployment (`POST /mcp`):

```bash
# Proxy stdio to a local running server (auto-resolving MCPConfig.base_path)
litestar --app my_app.main:app mcp bridge --base-url http://127.0.0.1:8000

# Proxy to a remote production endpoint with bearer authentication and custom headers
litestar --app my_app.main:app mcp bridge \
  --endpoint https://api.example.com/mcp \
  --bearer-env PROD_MCP_TOKEN \
  --header "X-Environment: production" \
  --sse-read-timeout 600
```

### Static Tool Manifest Export

Export the deterministic `tools/list` JSON manifest in CI pipelines without starting an HTTP server, and enforce description length bounds across all tools, input parameters, and output fields:

```bash
# Print the formatted tools/list manifest to stdout
litestar --app my_app.main:app mcp export-tool-spec

# Write the manifest to disk and fail with exit code 1 if any description violates length bounds
litestar --app my_app.main:app mcp export-tool-spec \
  --output artifacts/mcp-tools.json \
  --indent 2 \
  --strict-bounds
```

### Terminal Inspection

Inspect discovered MCP primitives or execute a tool directly from your development shell:

```bash
# Inspect all discovered MCP tools and their parameter signatures
litestar --app my_app.main:app mcp list-tools

# Inspect all discovered static resources and RFC 6570 URI templates
litestar --app my_app.main:app mcp list-resources

# Inspect all discovered MCP skills across configured sources
litestar --app my_app.main:app mcp list-skills

# Filter discovered MCP skills by repeatable tag and substring query
litestar --app my_app.main:app mcp list-skills --tag ops --query deploy

# Interactively invoke a discovered tool by name
litestar --app my_app.main:app mcp run
```
