# Marking Routes & Decorators

`litestar-mcp` discovers tools, resources, and prompts by inspecting Litestar route handlers at application startup (`on_app_init`). You can mark handlers using either **route keyword arguments** (`handler.opt`) or **dedicated decorators** (`@mcp_tool`, `@mcp_resource`, `@mcp_prompt`).

--8<-- "tools/docs/includes/diagram-primitives-and-discovery.html"

## Route Keyword Arguments vs Decorators

Litestar automatically forwards unknown keyword arguments on route decorators (`@get`, `@post`, `@put`, `@patch`, `@delete`, `@route`) into `handler.opt`. Passing `mcp_tool=True` (or a custom string name like `mcp_tool="get_user"`) directly to the route decorator is the most concise way to expose an existing endpoint.

The `@mcp_tool`, `@mcp_resource`, and `@mcp_prompt` decorators store metadata in `MetadataRegistry` and are useful when you want IDE autocompletion on MCP-specific options (`input_schema`, `output_schema`, `annotations`, `scopes`, `task`, `queue`) or when declaring standalone prompt functions.

=== "Route Kwargs"

    ```python title="docs/examples/snippets/marking_tools.py"
    --8<-- "docs/examples/snippets/marking_tools.py"
    ```

=== "Decorator"

    ```python title="docs/examples/snippets/marking_decorator.py"
    --8<-- "docs/examples/snippets/marking_decorator.py"
    ```

!!! tip "Choosing a Convention"
    Both forms compile into the exact same `Registry` entry on route handlers. When both a decorator and a `handler.opt` key define the same field on a route, `handler.opt` takes precedence. Note that `@mcp_prompt` on a plain function registers a standalone MCP-only prompt via `LitestarMCP(prompts=[...])`, whereas `mcp_prompt=True` on a Litestar route handler exposes the endpoint over both HTTP and `prompts/get`.

## Marking Tools, Resources, and Prompts

### Tool Markers

Pass `mcp_tool=True` to publish a route handler under its Python function name, or `mcp_tool="<custom_name>"` to override the advertised tool name:

```python title="docs/examples/snippets/marking_tools.py"
--8<-- "docs/examples/snippets/marking_tools.py"
```

### Resource Markers

Pass `mcp_resource=True` or `mcp_resource="<resource_name>"` to publish a read-only handler in `resources/list` and `resources/read`:

```python title="docs/examples/snippets/marking_resources.py"
--8<-- "docs/examples/snippets/marking_resources.py"
```

### Prompt Markers

Prompts can be registered either as route-bound handlers (`mcp_prompt=True` or `mcp_prompt="<prompt_name>"`) or as standalone `@mcp_prompt` callables passed to `LitestarMCP(prompts=[...])`:

```python title="docs/examples/snippets/marking_prompts.py"
--8<-- "docs/examples/snippets/marking_prompts.py"
```

## `MCPOptKeys` Reference Table

Downstream applications can customize every `handler.opt` key name via `MCPConfig(opt_keys=MCPOptKeys(...))` to avoid collisions with other plugins. All keys default to `mcp_<purpose>`:

| `MCPOptKeys` Attribute | Default `handler.opt` Key | Applies To | Description |
| :--- | :--- | :--- | :--- |
| `tool` | `"mcp_tool"` | Tools | Marks a route handler as an MCP tool (`True` uses the function name; a `str` sets an explicit tool name). |
| `resource` | `"mcp_resource"` | Resources | Marks a route handler as an MCP resource (`True` uses the function name; a `str` sets an explicit resource name). |
| `resource_uri` | `"mcp_resource_uri"` | Resources | Overrides the default `litestar://<name>` resource URI with a concrete URI. |
| `resource_mime_type` | `"mcp_resource_mime_type"` | Resources | Overrides the resource MIME type advertised in `resources/list` and used as a fallback in `resources/read`. |
| `resource_template` | `"mcp_resource_template"` | Resources | Declares an RFC 6570 Level 1 URI template (for example, `"app://workspaces/{workspace_id}/files/{file_id}"`). |
| `prompt` | `"mcp_prompt"` | Prompts | Marks a route handler as an MCP prompt (`True` uses the function name; a `str` sets an explicit prompt name). |
| `description` | `"mcp_description"` | Tools | Overrides the primary LLM-facing tool description. |
| `resource_description` | `"mcp_resource_description"` | Resources | Overrides the primary resource description (distinct from `mcp_description` so dual-role handlers can carry separate prose). |
| `prompt_description` | `"mcp_prompt_description"` | Prompts | Overrides the primary prompt description. |
| `prompt_title` | `"mcp_prompt_title"` | Prompts | Sets an optional human-readable display title for UI clients. |
| `prompt_arguments` | `"mcp_prompt_arguments"` | Prompts | Explicit `list[dict[str, Any]]` of prompt argument definitions (`name`, `title`, `description`, `required`). |
| `prompt_icons` | `"mcp_prompt_icons"` | Prompts | Optional `list[dict[str, Any]]` of MCP icon descriptors (`src`, `mimeType`, `sizes`). |
| `when_to_use` | `"mcp_when_to_use"` | Tools, Resources | Appends a structured `## When to use` section to the rendered description. |
| `returns` | `"mcp_returns"` | Tools, Resources | Appends a structured `## Returns` section to the rendered description. |
| `agent_instructions` | `"mcp_agent_instructions"` | Tools, Resources | Appends a mandatory `## Instructions` grounding section to the rendered description. |
| `annotations` | `"mcp_annotations"` | Tools | Mapping of MCP `ToolAnnotations` hints (`title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, `scopes`). |
| `read_only` | `"mcp_read_only"` | Tools | Boolean shorthand setting `annotations["readOnlyHint"]`. |
| `required_client_capabilities` | `"mcp_required_client_capabilities"` | Tools | Mapping of client capabilities required before `tools/call` is allowed (returns `-32021` when missing). |
| `task` / `task_support` | `"mcp_task"` / `"mcp_task_support"` | Tools | Declares MCP Tasks support mode (`"optional"`, `"required"`, `"forbidden"`, or aliases `True`, `False`, `"sync"`, `"disabled"`, `"inline"`). Can be set on a route, `Controller`, or `Router`. |
| `task_input_before_start` | `"mcp_task_input_before_start"` | Tools | Executes the initial `tools/call` synchronously so an `MCPInputRequiredResult` round completes before creating a background task. |
| `queue` | `"mcp_queue"` | Tools | Overrides the target `litestar-queues` queue lane name for task-augmented tool execution, or `False` / `"in_process"` to force in-process execution. |

## Description Precedence & Docstring Section Stripping

When rendering the LLM-facing `description` for a tool, resource, or prompt (`extract_description_sources` and `render_description`), `litestar-mcp` resolves prose in the following precedence order:

1. **Kind-Specific `handler.opt` Override**: `handler.opt["mcp_description"]` (tools), `handler.opt["mcp_resource_description"]` (resources), or `handler.opt["mcp_prompt_description"]` (prompts).
2. **Decorator Metadata**: `description=` passed to `@mcp_tool`, `@mcp_resource`, `@mcp_prompt`, `@tool`, or `@prompt`.
3. **Litestar Route OpenAPI Attributes**: `handler.description`, falling back to `handler.summary` (set via `@get(..., description="...", summary="...")`).
4. **Stripped Function Docstring (`strip_docstring_sections`)**: The function's `__doc__` with all Google-style (`Args:`, `Returns:`, `Raises:`, `Notes:`, `Examples:`), NumPy-style (`Parameters\n---`), and Sphinx (`:param:`, `:returns:`) sections automatically stripped.
5. **Deterministic Fallback**: `"Tool: <name>"`, `"Resource: <name>"`, or `"Prompt: <name>"`.

### Why Docstring Sections Are Stripped

Litestar route handlers frequently document dependency-injected parameters (such as `db_session`, `current_user`, or `settings`) in their `Args:` docstring block alongside client-supplied parameters. `strip_docstring_sections` removes the `Args:` / `Parameters:` / `Returns:` blocks from the top-level tool description so internal dependency names never leak into the LLM's tool summary, while per-parameter descriptions for advertised client arguments are still extracted and attached to their corresponding `inputSchema.properties.<param>.description` entries.

If `when_to_use`, `returns`, or `agent_instructions` are configured via `handler.opt` or `@mcp_tool` / `@mcp_resource`, `render_description` appends them as structured Markdown sections (`## When to use`, `## Returns`, `## Instructions`). CLI commands (`litestar mcp list-tools`, `litestar mcp list-resources`) pass `structured=False` to keep terminal output concise.

## Universal Dependency Injection

Because `litestar-mcp` dispatches tools, resources, and route-bound prompts through an internal ASGI scope (`execute_tool` and `execute_handler`), every dependency injection mechanism supported by your application works identically over Streamable HTTP, in-process `stdio`, and background `QueueTaskExecutor` workers:

- **Litestar `Provide` & `Dependency`**: Route-, controller-, router-, and app-level `dependencies={"key": Provide(...)}` and `NamedDependency[T]` parameters are resolved automatically and hidden from the advertised MCP `inputSchema` (unless a provider itself takes client-facing input parameters).
- **Dishka (`FromDishka[T]`)**: Handlers using `FromDishka[T]` are unwrapped via `__dishka_orig_func__` during schema generation so `FromDishka` parameters are excluded from `inputSchema` and resolved from the active `Scope.REQUEST` Dishka container at execution time.
- **Standalone `@tool` DI (`ToolContext`, `Inject()`, `Depends(fn)`)**: Standalone `@tool` functions and `SkillController` methods can declare `ToolContext`, `Inject("key")` (resolving from `app.dependencies` or `app.state`), `Depends(fn)`, or `FromDishka[T]` parameters alongside client arguments.

```python title="docs/examples/snippets/marking_dependencies.py"
--8<-- "docs/examples/snippets/marking_dependencies.py"
```
