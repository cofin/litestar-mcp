# Prompts & `PromptController`

Prompts are reusable, parameterized instruction templates exposed via `prompts/list` and `prompts/get`. Unlike tools (which execute side effects or computations) and resources (which return raw state), a prompt returns a structured sequence of `PromptMessage` objects that the client feeds into an LLM conversation.

`litestar-mcp` supports three ways to author prompts:

1. **Standalone `@mcp_prompt` functions** registered via `LitestarMCP(prompts=[...])`.
2. **Route-bound `mcp_prompt=True` handlers** exposed over both HTTP and `prompts/get`.
3. **Class-based `PromptController` subclasses** grouping related `@prompt` methods under a shared namespace `prefix`, prepended `instructions`, Litestar `guards`, and dependency injection (`Provide`, `FromDishka[T]`).

## Standalone & Route-Bound Prompts

Use `@mcp_prompt` for MCP-only prompt templates, or tag an existing Litestar route handler with `mcp_prompt=True` (or `mcp_prompt="<name>"`) when the prompt should also be callable as a standard HTTP endpoint:

```python title="docs/examples/snippets/marking_prompts.py"
--8<-- "docs/examples/snippets/marking_prompts.py:example"
```

For route-bound prompts, you can customize metadata with `mcp_prompt_description`, `mcp_prompt_title`, `mcp_prompt_arguments`, and `mcp_prompt_icons` (see the [MCPOptKeys Reference Table](marking-routes.md#mcpoptkeys-reference-table)). When `arguments` is omitted, `litestar-mcp` introspects the callable's signature, excludes DI parameters, and populates argument descriptions from the function's Google-style `Args:` docstring section.

## Class-Based PromptController

When building domain-specific prompt suites or multi-agent applications, subclass `PromptController` and decorate methods with `@prompt`:

```python title="docs/examples/agent/coffee_shop_agent.py"
--8<-- "docs/examples/agent/coffee_shop_agent.py:70:92"
```

Register a `PromptController` either by adding it to `Litestar(route_handlers=[CoffeePromptController])` or by passing it in `MCPConfig(prompt_controllers=[CoffeePromptController])`.

### PromptController Capabilities

- **Namespace `prefix`**: Setting `prefix = "coffee"` registers `@prompt` methods as `coffee/welcome_customer` and `coffee/roast_notes` (`qualify_prompt_name`).
- **Prepended `instructions` (`apply_prompt_instructions`)**: When `instructions` is non-empty on a `PromptController`, `prompts/get` automatically prepends `f"{instructions}\n\n"` to the text of the first `"user"` role message (or inserts a leading `"user"` text message if no user text message is present).
- **Internal Dispatch Protection**: Each `@prompt` method is mounted on an internal synthetic controller path (`/_litestar_mcp/<module>.<qualname>`) with `include_in_schema=False` and protected by the `require_internal_dispatch` guard so it can only be invoked through MCP internal ASGI dispatch (`execute_handler`), never as a public unguarded HTTP POST route.
- **Controller `guards` and Universal DI**: Because `PromptController` subclasses `litestar.Controller`, you can declare `guards = [...]` and `dependencies = {"key": Provide(...)}` at the class level, and inject `Provide` dependencies or Dishka `FromDishka[T]` parameters directly into `@prompt` methods.

## Return Value Normalization

Every prompt callable or `@prompt` method can return any of the following Python types, which `_normalize_prompt_result` converts into the canonical MCP `messages` list:

| Return Type | Normalized Wire Output (`result.messages`) |
| :--- | :--- |
| `str` | `[{"role": "user", "content": {"type": "text", "text": "<value>"}}]` |
| `PromptMessage` | `[message.to_mcp_dict()]` (`PromptMessage.to_mcp_dict()`; use `PromptMessage.user("...")` or `PromptMessage.assistant("...")`) |
| `dict[str, Any]` with `"messages"` | Used directly as the top-level `prompts/get` result dictionary (with controller `instructions` applied to `result["messages"]`). |
| `dict[str, Any]` with `"role"` & `"content"` | Wrapped in a single-element list `[result]`. |
| `Sequence[PromptMessage]` or `list[dict]` | Each element is normalized in order into a `PromptMessage` dictionary. |
| Any other object | Coerced via `str(value)` into a single `"user"` text message. |

## Capability Gating & Error Contract

- **Capability Gating**: `server/discover` and `initialize` advertise `"prompts": {"listChanged": True}` under `capabilities` **only** when at least one visible prompt is registered.
- **String-Only Wire Arguments**: Per the MCP specification, all values inside `params.arguments` on `prompts/get` must be strings. Passing a non-string argument value, omitting a required argument, or passing an unknown argument name raises JSON-RPC `INVALID_PARAMS` (`-32602`).
- **Execution Errors**: If a prompt callable or route handler raises an exception or returns an HTTP error status during `prompts/get`, `MCPHandlerService` raises JSON-RPC `INTERNAL_ERROR` (`-32603`) with `"message": "Prompt execution failed"` and preserves the HTTP status code in `error.data.statusCode`.
