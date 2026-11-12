# Prompts & `PromptController`

Prompts are reusable, parameterized instruction templates exposed via `prompts/list` and `prompts/get`. Unlike tools (which execute side effects or computations) and resources (which return raw state), a prompt returns a structured sequence of `PromptMessage` objects that the client feeds into an LLM conversation.

`litestar-mcp` supports flexible ways to author and project prompts:

1. **Standalone `@mcp_prompt` functions** registered via `LitestarMCP(prompts=[...])` or `MCP.prompt(...)`.
2. **Route-bound `mcp_prompt=True` handlers** exposed over both HTTP and `prompts/get`.
3. **Class-based `PromptController` subclasses** grouping related `@prompt` methods under a shared namespace `prefix`, prepended `instructions`, Litestar `guards`, and dependency injection (`Provide`, `FromDishka[T]`).
4. **Opt-in Agent Skills projection (`MCPSkillsConfig(expose_as_prompts=True)`)** dynamically exposing visible `SKILL.md` bundles in `prompts/list` and `prompts/get`.

## Standalone & Route-Bound Prompts

Use `@mcp_prompt` for MCP-only prompt templates, or tag an existing Litestar route handler with `mcp_prompt=True` (or `mcp_prompt="<name>"`) when the prompt should also be callable as a standard HTTP endpoint:

```python title="docs/examples/snippets/marking_prompts.py"
--8<-- "docs/examples/snippets/marking_prompts.py:example"
```

For route-bound prompts, you can customize metadata with `mcp_prompt_description`, `mcp_prompt_title`, `mcp_prompt_arguments`, `mcp_prompt_icons`, and `mcp_prompt_meta` (see the [MCPOptKeys Reference Table](marking-routes.md#mcpoptkeys-reference-table)). You can also pass `meta=` on `@mcp_prompt(..., meta=...)`, `@prompt(..., meta=...)` on a `PromptController`, and standalone `MCP.prompt(..., meta=...)` to attach extension metadata emitted as `_meta` in `prompts/list`:

- **Shallow-Merge Precedence**: When a route handler sets both `@mcp_prompt(..., meta=...)` and route `opt` `mcp_prompt_meta={...}`, `mcp_prompt_meta` overrides matching keys from decorator `meta` while retaining non-colliding decorator keys. Empty `{}` mappings are omitted on the wire.
- **Automatic Argument Introspection**: When `arguments` is omitted, `litestar-mcp` introspects the callable's signature, excludes DI parameters, and populates argument descriptions from the function's Google-style `Args:` docstring section.

```python
from litestar_mcp import PromptController, mcp_prompt, prompt


@mcp_prompt(
    "triage_incident",
    title="Triage Incident",
    meta={"category": "sre", "owner": "platform"},
)
def triage_incident(service: str) -> str:
    """Build an incident triage checklist for a target service."""
    return f"Review latency, error rates, and saturation for {service}."


class OpsPromptController(PromptController):
    prefix = "ops"

    @prompt(title="Release Readiness", meta={"stage": "production"})
    async def release_check(self, version: str) -> str:
        """Verify rollout gates for a scheduled release."""
        return f"Validate canary metrics and rollback plan for {version}."
```

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

## Projecting Agent Skills as Prompts

Enabling `MCPSkillsConfig(expose_as_prompts=True)` on `MCPConfig.skills` dynamically projects every visible SEP-2640 skill from `SkillCatalog` into `prompts/list` and `prompts/get` without duplicating `SKILL.md` content:

- **Sorted Catalog Projection**: Visible skills (after applying `MCPConfig` tag and operation filters) appear sorted by `name` in `prompts/list` with `arguments: []`, preserving optional `title`, `icons`, and `_meta` from `SKILL.md` frontmatter or `Skill` definitions.
- **Explicit Prompt Precedence**: Registered `@mcp_prompt` callables, route-bound `mcp_prompt` handlers, and `PromptController` `@prompt` methods always take precedence over projected skills when a name collides.
- **Digest-Verified Rendering**: Calling `prompts/get` for a projected skill reads its SHA-256 digest-verified `SKILL.md` through `SkillCatalog` and returns the body as a single `"user"` `PromptMessage`. Passing non-empty `params.arguments` to a zero-argument projected skill fails with `INVALID_PARAMS` (`-32602`), and any SHA-256 digest mismatch (`SkillIntegrityError`) maps to `INTERNAL_ERROR` (`-32603`). See [Agent Skills (SEP-2640)](skills.md) for full configuration details.

## Capability Gating & Error Contract

- **Capability Gating (`capabilities.prompts`)**: `server/discover` and `initialize` advertise `capabilities.prompts` (`"prompts": {"listChanged": True}`) whenever at least one visible prompt is registered or `MCPSkillsConfig(expose_as_prompts=True)` is enabled on `MCPConfig.skills` (even when zero explicit `@mcp_prompt` or `PromptController` prompts are registered).
- **String-Only Wire Arguments**: Per the MCP specification, all values inside `params.arguments` on `prompts/get` must be strings. Passing a non-string argument value, omitting a required argument, or passing an unknown argument name raises JSON-RPC `INVALID_PARAMS` (`-32602`).
- **Execution Errors**: If a prompt callable or route handler raises an exception or returns an HTTP error status during `prompts/get`, `MCPHandlerService` raises JSON-RPC `INTERNAL_ERROR` (`-32603`) with `"message": "Prompt execution failed"` and preserves the HTTP status code in `error.data.statusCode`.
