# Configuration (`MCPConfig`)

The `LitestarMCP` plugin is configured through `MCPConfig`, along with companion dataclasses for custom `handler.opt` key names (`MCPOptKeys`), background task execution (`MCPTaskConfig`), and Agent Skills (`MCPSkillsConfig`).

## Minimal Setup

When `LitestarMCP()` is registered without arguments, it uses the default `MCPConfig`, mounts the Streamable HTTP controller at `/mcp`, derives the server name and version from the application's OpenAPI configuration, and discovers all marked routes.

```python title="docs/examples/snippets/configuration_minimal.py"
--8<-- "docs/examples/snippets/configuration_minimal.py:example"
```

## Custom Configuration

Override `base_path`, `name`, `include_in_schema`, filtering rules, or protocol extensions by passing an `MCPConfig` instance to `LitestarMCP`:

```python title="docs/examples/snippets/configuration_custom.py"
--8<-- "docs/examples/snippets/configuration_custom.py:example"
```

## Standalone Prompts & Controllers

Standalone prompt functions decorated with `@mcp_prompt` can be passed to `LitestarMCP(config, prompts=[...])`, while class-based `PromptController` and `SkillController` types can either be included in `app.route_handlers` or registered via `MCPConfig(prompt_controllers=[...], skill_controllers=[...])`:

```python title="docs/examples/snippets/configuration_prompts.py"
--8<-- "docs/examples/snippets/configuration_prompts.py:example"
```

## Complete MCPConfig Reference

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `base_path` | `str` | `"/mcp"` | Base URL path where `MCPController` mounts its Streamable HTTP `POST` and `DELETE` endpoints. |
| `include_in_schema` | `bool` | `False` | Whether to include the mounted MCP transport routes in Litestar's generated OpenAPI schema. |
| `name` | `str or None` | `None` | Optional server name override. When `None`, falls back to `app.openapi_config.title` (or `"Litestar MCP Server"`). Server version is derived from `app.openapi_config.version` (default `"1.0.0"`), and the application's OpenAPI document is always served via the built-in `litestar://openapi` resource. |
| `instructions` | `str or None` | `None` | Optional server-level grounding instructions returned in `server/discover` and `initialize` responses. |
| `auth` | `Any or None` | `None` | Optional authentication policy (for example, `required("api-key")` from `litestar-security`) applied to the mounted MCP router via `route_opt["auth"]` when `route_opt` does not already define `"auth"`. |
| `guards` | `list[Any] or None` | `None` | Litestar guard callables applied to the mounted MCP router. |
| `route_opt` | `dict[str, Any] or None` | `None` | Optional `opt` dictionary applied to the mounted MCP router and merged into the JSON-RPC handler (for example, `{"auth": required("api-key")}` with `litestar-security`). |
| `allowed_origins` | `list[str] or None` | `None` | Exact additional `Origin` header values accepted alongside the request's own origin. Mismatched `Origin` headers are rejected with `403 Forbidden`. |
| `include_operations` | `list[str] or None` | `None` | Optional allowlist of tool, resource, or prompt names to expose over MCP. |
| `exclude_operations` | `list[str] or None` | `None` | Optional denylist of tool, resource, or prompt names to hide from MCP. |
| `include_tags` | `list[str] or None` | `None` | Optional allowlist of Litestar route handler `tags` to expose over MCP. |
| `exclude_tags` | `list[str] or None` | `None` | Optional denylist of Litestar route handler `tags` to hide from MCP. |
| `opt_keys` | `MCPOptKeys` | `MCPOptKeys()` | Configurable `handler.opt` key names inspected during route discovery. |
| `infer_http_method_annotations` | `bool` | `True` | Automatically infer default MCP tool annotation hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`) from the route handler's HTTP verb when not explicitly overridden (`mcp_annotations` / `annotations`). |
| `max_tool_description_length` | `int or None` | `None` | Optional maximum character length for tool descriptions enforced during `validate_tool_descriptions` and `litestar mcp export-tool-spec`. |
| `max_param_description_length` | `int or None` | `None` | Optional maximum character length for tool input parameter descriptions enforced during tool-spec validation. |
| `max_output_field_description_length` | `int or None` | `None` | Optional maximum character length for tool output schema property descriptions enforced during tool-spec validation. |
| `tasks` | `bool or MCPTaskConfig or None` | `None` | Enables the `io.modelcontextprotocol/tasks` extension. Pass `True` for default `MCPTaskConfig()`, an `MCPTaskConfig` instance, or `False` to disable even when `QueuePlugin` is present. |
| `skills` | `MCPSkillsConfig or None` | `None` | Enables the `io.modelcontextprotocol/skills` (SEP-2640) extension from static `paths` and/or dynamic `sources`. |
| `prompt_controllers` | `Sequence[type[Any]]` | `()` | Sequence of `PromptController` subclasses or instances to mount on startup. |
| `skill_controllers` | `Sequence[type[Any]]` | `()` | Sequence of `SkillController` subclasses or instances to mount on startup. |
| `before_tool_call` | `BeforeToolCallHook or None` | `None` | Optional sync or async callback invoked before each `tools/call` dispatch, after the synthetic request is built and before guards execute. |
| `after_tool_call` | `AfterToolCallHook or None` | `None` | Optional sync or async callback invoked after each `tools/call` dispatch with `result`, `exception`, and `duration` in seconds. |
| `cache_ttl_ms` | `int` | `0` | Cache TTL hint in milliseconds (`ttlMs`) attached to cacheable discovery, list, and resource-read results (`0` disables caching). |
| `cache_scope` | `Literal["private", "public"]` | `"private"` | Cache visibility scope (`cacheScope`) attached alongside `ttlMs`. Keep `"private"` whenever tools, resources, or skills depend on caller identity. |
| `subscription_max_streams` | `int` | `10_000` | Maximum concurrent `subscriptions/listen` SSE streams permitted per process. |
| `subscription_keepalive_seconds` | `float` | `15.0` | Interval in seconds between `: keepalive` SSE comments on active subscription streams. |
| `subscription_channels` | `Any or None` | `None` | Optional Litestar `ChannelsPlugin` backend used to fan out subscription notifications across worker processes. |
| `stream_queue_capacity` | `int` | `256` | Bounded in-memory queue capacity per active progress or subscription stream (`stream_queue_capacity`). Progress streams apply backpressure; slow subscription consumers are disconnected cleanly. |
| `stream_cleanup_timeout` | `float` | `5.0` | Positive finite timeout in seconds allowed for cooperative progress producer cleanup after a response stream closes. |
| `list_page_size` | `int` | `100` | Positive integer page size for `tools/list`, `resources/list`, `resources/templates/list`, `prompts/list`, `skills/list`, and `resources/directory/read`. |
| `max_blob_bytes` | `int or None` | `26_214_400` (`25 MiB`) | Maximum raw byte size for base64-embedded binary blobs in tool results and `resources/read` responses (`None` disables the cap). |

!!! note "Server Metadata and OpenAPI Derivation"
    `LitestarMCP` reads `app.openapi_config.title` (when `MCPConfig.name` is `None`) and `app.openapi_config.version` to populate `serverInfo` in `initialize` and `_meta["io.modelcontextprotocol/serverInfo"]` in JSON-RPC responses. The application's live OpenAPI schema is exposed automatically at the `litestar://openapi` MCP resource URI regardless of the HTTP `openapi_config.path` setting.

## Operation and Tag Filtering Precedence

`MCPConfig` provides four filtering lists to narrow which marked routes are exposed on the MCP surface:

- `exclude_tags`: Denylist of Litestar route `tags`.
- `include_tags`: Allowlist of Litestar route `tags`.
- `exclude_operations`: Denylist of MCP primitive names (tool, resource, or prompt names).
- `include_operations`: Allowlist of MCP primitive names.

`should_include_handler` evaluates filters in strict order of precedence: **`exclude > include`** and **`tags > operations`**:

1. If `exclude_tags` is set and the handler carries any excluded tag, the handler is **excluded**.
2. If `include_tags` is set and the handler carries none of the included tags, the handler is **excluded**.
3. If `exclude_operations` is set and the primitive name is in `exclude_operations`, the handler is **excluded**.
4. If `include_operations` is set and the primitive name is not in `include_operations`, the handler is **excluded**.
5. Otherwise, the handler is **included**.

Filters apply identically to `*/list` responses and direct invocation (`tools/call`, `resources/read`, `prompts/get`). Calling a filtered tool, resource, or prompt returns the same `INVALID_PARAMS` (`-32602`) not-found response as an unregistered name.

!!! warning "Filtering vs Authorization"
    Tag and operation filters control global catalog visibility across the entire MCP server. For per-request, per-principal access control, attach Litestar `guards` or `litestar-security` `MCPConfig(auth=...)` and `route_opt` policies—see [Authorization, Guards & Scopes](../security/authorization-and-scopes.md).

## Enabling MCP Tasks (MCPTaskConfig)

Pass `tasks=True` or an `MCPTaskConfig` instance to enable the `io.modelcontextprotocol/tasks` extension:

```python title="docs/examples/snippets/configuration_tasks.py"
--8<-- "docs/examples/snippets/configuration_tasks.py:example"
```

When `litestar-queues` `QueuePlugin` is registered on the same `Litestar` application, `LitestarMCP` automatically enables MCP Tasks and wires `QueueTaskExecutor` with zero configuration. Install the optional `queues` extra to distribute background MCP Tasks across `litestar-queues` workers:

=== "uv"

    ```bash
    uv add "litestar-mcp[queues]"
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[queues]"
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[queues]"
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[queues]"
    ```

See [MCP Tasks](../queues/mcp-tasks.md) and [Distributed Task Queues](../queues/distributed-task-queues.md) for `MCPTaskConfig` options (`store`, `default_ttl_ms`, `max_ttl_ms`, `poll_interval_ms`, `status_callback`, `executor`) and worker rehydration details.

## Enabling Agent Skills (MCPSkillsConfig)

Pass an `MCPSkillsConfig` instance to `MCPConfig(skills=...)` to advertise and serve Agent Skills (`io.modelcontextprotocol/skills`, SEP-2640) from filesystem directories (`paths`) or dynamic request-aware `SkillSource` providers (`sources`):

```python title="docs/examples/snippets/configuration_skills.py"
--8<-- "docs/examples/snippets/configuration_skills.py:example"
```

See [Agent Skills (SEP-2640)](skills.md) for directory layout rules, startup validation, wire methods, and dynamic `SkillSource` composition.

## Tool-Call Lifecycle Callbacks

Configure `before_tool_call` and `after_tool_call` on `MCPConfig` to run audit logging, custom metrics, or structured tracing around every `tools/call` invocation independently of route ownership layers:

```python title="docs/examples/snippets/configuration_tool_callbacks.py"
--8<-- "docs/examples/snippets/configuration_tool_callbacks.py:example"
```

- **`before_tool_call(tool_name, arguments, request, /)`**: Invoked once per `tools/call` after the synthetic internal HTTP `Request` is constructed and before Litestar guards execute. Receives a shallow copy of `arguments`.
- **`after_tool_call(tool_name, arguments, request, /, *, result, exception, duration)`**: Invoked in a `finally` block after every `tools/call`—including normal completions, guard rejections, validation errors, and unhandled exceptions—with the elapsed `duration` in seconds.
- Any exception raised inside `before_tool_call` or `after_tool_call` is logged and swallowed so observability hooks never corrupt tool execution results.

## Opaque-Cursor List Pagination

All six catalog enumeration methods—`tools/list`, `resources/list`, `resources/templates/list`, `prompts/list`, `skills/list`, and `resources/directory/read`—implement the MCP opaque-cursor pagination contract:

1. Each method accepts an optional string `params.cursor`.
2. The server slices the sorted catalog using `MCPConfig.list_page_size` (default `100`, must be a positive integer).
3. When additional items remain after the current slice, the response includes `"nextCursor": "<opaque-base64-token>"`.
4. Clients pass `nextCursor` back as `params.cursor` on the next request until `nextCursor` is omitted.
5. Supplying a non-string, malformed, or negative-offset cursor raises JSON-RPC `INVALID_PARAMS` (`-32602`).
