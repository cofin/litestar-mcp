# Fix: Plugin-registered dependencies break MCP tool execution

**Date:** 2026-04-10
**Issue:** [cofin/litestar-mcp#19](https://github.com/cofin/litestar-mcp/issues/19)
**Branch:** `feat/hasansezertasan/fix-dependency-injection`
**Status:** Design approved, ready for implementation plan

## Problem

When `SQLAlchemyPlugin` (or any plugin that registers app-level dependencies) is loaded alongside `LitestarMCP`, **every** MCP tool invocation fails — including tools that do not consume those dependencies at all.

Root cause: `litestar_mcp.executor._resolve_dependencies` calls `handler.resolve_dependencies()`, which returns the **merged dependency registry** across all scope layers (app → router → controller → handler), then eagerly calls **every** provider in that registry. Request-scoped providers such as `db_engine` / `db_session` raise `NotCallableInCLIContextError` when called without a request context, aborting tool execution even for handlers that never declared those dependencies.

## Non-Goals

The following are explicitly out of scope for this change and will be tracked separately:

- Opening a real request scope inside `execute_tool` to support handlers that *do* consume `db_session` / Advanced Alchemy sessions.
- Per-tool transaction lifecycle management (commit/rollback semantics).
- Any change to how Litestar plugins register dependencies.
- Type coercion of tool arguments against dependency provider signatures.

Handlers that legitimately need request-scoped resources will continue to raise a (clearer) error; this spec does not attempt to make them MCP-callable.

## Design

### Guiding principle

Mirror Litestar's own DI semantics: **only resolve dependencies that are transitively consumed by the handler's signature.** Litestar's request pipeline walks outward from the handler signature, pulling providers only when some consumer declares them as a kwarg. The MCP executor should do the same.

### Components

#### 1. New helper: `_collect_consumed_dependencies`

```python
def _collect_consumed_dependencies(
    fn: Callable[..., Any],
    registry: dict[str, Any],
) -> set[str]:
    """Return the transitive closure of dependency names consumed by fn.

    Starting from fn's signature parameters, walks the registry to collect
    every dependency name that fn — or any provider fn transitively depends
    on — actually declares as a kwarg. Names not present in the registry
    are ignored (they are either tool arguments, `self`, or reserved
    framework parameters).

    Cycles are broken via a visited set.
    """
```

- Uses `inspect.signature(callable).parameters` at each step.
- For `Provide` objects, inspects the underlying `.dependency` attribute's signature.
- Returns names only; resolution order is computed at call time in `_resolve_dependencies`.

#### 2. Modified: `_resolve_dependencies`

- Accepts the consumed set and resolves providers in dependency-first (topological) order so that, e.g., `retrieve_user(role: Role)` receives an already-resolved `role` kwarg.
- Providers whose name is not in the consumed set are **never called**.
- Reserved-name check (`request`, `socket`, `headers`, `cookies`, `query`, `body`, `state`, `scope`) runs against the **consumed set**, not the full registry.
- When a provider in the consumed set fails to invoke, raises `NotCallableInCLIContextError` with the improved message (see below).

#### 3. Error message improvement

When a consumed provider cannot be called with no arguments (i.e., it is genuinely request-scoped), raise:

> `Tool '{handler}' cannot be executed via MCP: its dependency '{dep}' requires request context (e.g. a plugin-registered session, request, or connection-scoped resource). Tools that need such resources are not currently MCP-callable.`

The existing exception class `NotCallableInCLIContextError` is retained; only the message template changes. Its constructor signature stays the same so existing tests continue to pass.

### Data flow

```
execute_tool(handler, app, tool_args)
  │
  ├─ fn = get_handler_function(handler)
  ├─ registry = handler.resolve_dependencies()
  ├─ consumed = _collect_consumed_dependencies(fn, registry)
  ├─ _reserved_check(consumed)           # was: whole registry
  ├─ deps = _resolve_dependencies(       # only calls providers in `consumed`
  │         handler, fn, registry, consumed)
  ├─ call_args = {**deps, **tool_args-mapped-to-sig}
  └─ invoke fn(**call_args)
```

### Error handling

| Situation | Behavior |
|---|---|
| Handler declares no plugin deps, registry contains `db_engine` | `db_engine` is **not** resolved; handler runs. (This is the bug fix.) |
| Handler declares `user: User`, `retrieve_user` needs `db_session` | Both are consumed transitively; if `db_session` is request-scoped, raises improved error naming `db_session`. |
| Handler declares `request: Request` directly | Reserved-name check raises `NotCallableInCLIContextError` (same as today). |
| Cyclic provider graph (pathological) | Visited-set breaks the cycle on second encounter; no infinite loop. If a cyclic back-edge leaves a required kwarg unresolved, raises `NotCallableInCLIContextError` naming the dep. |
| Provider in consumed set raises unrelated error | Wrapped in `NotCallableInCLIContextError` with improved message (same policy as today, just naming the right dep). |

## Test Plan (TDD)

Tests live in `tests/test_executor.py`. Written first, must fail against current `main`, then pass after the fix.

1. **`test_unused_plugin_dependency_is_not_resolved`** — exact reproduction of issue #19. Register an app-level `db_engine` provider that raises `RuntimeError("should never be called")`. Handler takes no params. Assert execution succeeds.
2. **`test_unused_plugin_dependency_with_handler_args`** — same registry; handler takes `name: str`. Assert tool args flow through, provider is not called.
3. **`test_transitive_dependency_resolution`** — handler declares `user: User`; `retrieve_user` provider declares `role: Role`; `role` provider has no deps. Assert all three resolve in the correct order and are passed as kwargs.
4. **`test_consumed_request_scoped_dep_raises_clear_error`** — handler directly declares `db_session`. Assert `NotCallableInCLIContextError` is raised and the message contains `db_session` and the phrase `request context`.
5. **`test_cyclic_dependency_does_not_infinite_loop`** — two providers referencing each other's names. Assert the visited-set breaks the cycle and execution terminates without hanging. Behavior: the second visit of a name is skipped (not re-resolved), and whichever provider is reached first resolves with `None`/default for the back-edge kwarg if that kwarg has a default, otherwise raises `NotCallableInCLIContextError` naming the cyclic dep. This is pathological input — the goal is "does not hang", not "magically resolves cycles".
6. **Regression guard:** the existing tests `test_cli_context_limitation_request_dependency`, `test_dependency_resolution_failure`, `test_execute_tool_with_request_dependency_error`, and `test_execute_handler_with_dependencies` must continue to pass without modification.

## Files Touched

- `litestar_mcp/executor.py` — add `_collect_consumed_dependencies`, modify `_resolve_dependencies` and `execute_tool`, update `NotCallableInCLIContextError` message template.
- `tests/test_executor.py` — add the six tests above.
- `CHANGELOG.md` — add a Fixed entry referencing issue #19.

No changes to public API, config, or plugin surface.

## Acceptance Criteria

- [ ] Reproduction script from issue #19 runs successfully: `health_check` returns `{"status": "ok"}` via MCP even with `SQLAlchemyPlugin` registered.
- [ ] All six new tests pass.
- [ ] Full existing test suite passes unchanged.
- [ ] A handler that directly declares `db_session` receives the improved error message.
