# Fix Plugin Dep Injection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `litestar_mcp.executor` so plugin-registered dependencies (e.g. `SQLAlchemyPlugin`'s `db_engine` / `db_session`) no longer break MCP tool execution for handlers that never declared them, by resolving only the transitive closure of dependencies consumed by the handler signature.

**Architecture:** Add a new helper `_collect_consumed_dependencies` that walks a handler's signature transitively through the dependency registry to compute exactly which providers must be resolved. Modify `_resolve_dependencies` to honor that set and to resolve providers in dependency-first (topological) order. Improve `NotCallableInCLIContextError`'s message for consumed-but-unresolvable deps. No public API changes.

**Tech Stack:** Python 3.12+, Litestar 2.21+, pytest, pytest-asyncio, `uv`. Linked issue: cofin/litestar-mcp#19. Spec: `docs/superpowers/specs/2026-04-10-fix-plugin-dep-injection-design.md`.

---

## File Structure

**Files to modify:**

- `litestar_mcp/executor.py` — Add `_collect_consumed_dependencies`. Modify `_resolve_dependencies` to accept a consumed set and resolve topologically. Modify `execute_tool` to call the new helper and pass the consumed set. Update `NotCallableInCLIContextError` message template.
- `tests/test_executor.py` — Add six new tests (one per spec test), keep all existing tests passing without modification.
- `CHANGELOG.md` — Add a `### Fixed` entry under the next release section referencing issue #19.

**New files:** none.

**Files not touched:** `litestar_mcp/utils.py`, `litestar_mcp/plugin.py`, `tests/conftest.py`. `create_app_with_handler` already supports a `dependencies` kwarg (passed through to the Litestar decorator via `**handler_kwargs`) — no test helper changes needed.

---

## Background Notes for the Engineer

You probably do not know this codebase. Read before starting:

1. **Litestar DI model** — Handlers declare dependencies as kwargs in their signature. Providers are registered via `dependencies={"name": Provide(fn)}` at app / router / controller / handler level. When a request comes in, Litestar walks from the handler's signature outward: for each kwarg that matches a registered provider, it resolves that provider (recursively resolving *its* kwargs the same way). **Only dependencies actually consumed (directly or transitively) get called.**

2. **Why the bug exists** — `handler.resolve_dependencies()` returns the **merged registry** of every provider in scope (app + router + controller + handler), *not* the set that will actually run for a given request. The MCP executor treats that registry as a "must resolve everything" list and calls every provider with no args, breaking on the first request-scoped one.

3. **`Provide` object shape** — A `Provide` instance has a `.dependency` attribute holding the actual callable. Use `inspect.signature(provide.dependency).parameters` to discover its kwargs. Some entries in the registry may already be unwrapped functions (test edge cases / fallback paths); handle both with `getattr(provider, "dependency", provider)`.

4. **Reserved names** — Litestar reserves certain parameter names (`request`, `socket`, `headers`, `cookies`, `query`, `body`, `state`, `scope`) for framework-level injection. These cannot be called like ordinary providers in a CLI/MCP context. The current code already tracks a subset; we keep the same policy.

5. **Test helper** — `tests/conftest.py::create_app_with_handler(fn, dependencies=...)` builds a real Litestar app with the given handler and returns `(app, handler)`. Passing `dependencies={"foo": Provide(provider_fn, sync_to_thread=False)}` registers them as handler-level deps. For app-level deps (needed to reproduce the issue #19 scenario), you'll build the `Litestar` object directly — see Task 1 for the exact pattern.

6. **Commits** — The project uses Conventional Commits (see `git log --oneline`). Use `fix(executor): ...` for the bug-fix commits, `test(executor): ...` for pure test additions, `docs(changelog): ...` for the CHANGELOG entry.

7. **Running a single test:** `uv run pytest tests/test_executor.py::TestExecutor::test_name -v`. Running the full suite: `uv run pytest -q`. Running with the reproduction script from issue #19 is **not** part of the test suite — it's a manual integration check at the end.

---

## Task 1: Red — unused plugin dependency is not resolved

**Files:**
- Modify: `tests/test_executor.py` (add test inside `class TestExecutor`)

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_executor.py` at the end of `class TestExecutor`:

```python
@pytest.mark.asyncio
async def test_unused_plugin_dependency_is_not_resolved(self) -> None:
    """Regression for #19: app-level deps not consumed by handler must not be called.

    Reproduces the SQLAlchemyPlugin scenario: an app-level `db_engine` dep is
    registered, but the handler doesn't declare it. The provider must NOT be
    invoked, so the tool can execute successfully.
    """
    from litestar import Litestar, get
    from litestar.di import Provide

    from tests.conftest import get_handler_from_app

    provider_was_called = False

    def broken_db_engine() -> str:
        nonlocal provider_was_called
        provider_was_called = True
        msg = "db_engine should never be called — handler doesn't consume it"
        raise RuntimeError(msg)

    @get("/health", sync_to_thread=False)
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app = Litestar(
        route_handlers=[health_check],
        dependencies={"db_engine": Provide(broken_db_engine, sync_to_thread=False)},
    )
    handler = get_handler_from_app(app, "/health", "GET")

    result = await execute_tool(handler, app, {})

    assert result == {"status": "ok"}
    assert provider_was_called is False, (
        "db_engine provider was called despite handler not declaring it"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_executor.py::TestExecutor::test_unused_plugin_dependency_is_not_resolved -v`

Expected: **FAIL** with `NotCallableInCLIContextError` mentioning `db_engine` (because the current executor calls every provider in the registry, and `broken_db_engine` raises `RuntimeError`, which gets wrapped into `NotCallableInCLIContextError`).

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_executor.py
git commit -m "test(executor): add failing test for unused plugin dep (#19)"
```

---

## Task 2: Red — unused plugin dep does not interfere with handler arguments

**Files:**
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write the failing test**

Append to `class TestExecutor`:

```python
@pytest.mark.asyncio
async def test_unused_plugin_dependency_with_handler_args(self) -> None:
    """Same registry as #19 scenario, but handler also takes a tool arg.

    Ensures the fix doesn't accidentally drop or rename real parameters.
    """
    from litestar import Litestar, get
    from litestar.di import Provide

    from tests.conftest import get_handler_from_app

    def broken_db_engine() -> str:
        msg = "should not be called"
        raise RuntimeError(msg)

    @get("/greet", sync_to_thread=False)
    def greet(name: str) -> dict[str, str]:
        return {"hello": name}

    app = Litestar(
        route_handlers=[greet],
        dependencies={"db_engine": Provide(broken_db_engine, sync_to_thread=False)},
    )
    handler = get_handler_from_app(app, "/greet", "GET")

    result = await execute_tool(handler, app, {"name": "Alice"})
    assert result == {"hello": "Alice"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_executor.py::TestExecutor::test_unused_plugin_dependency_with_handler_args -v`

Expected: **FAIL** with `NotCallableInCLIContextError` about `db_engine`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_executor.py
git commit -m "test(executor): add failing test for unused dep with handler args (#19)"
```

---

## Task 3: Green — add `_collect_consumed_dependencies` and use it

**Files:**
- Modify: `litestar_mcp/executor.py`

- [ ] **Step 1: Add the helper function**

In `litestar_mcp/executor.py`, add this helper immediately above `_check_unsupported_dependency`:

```python
def _provider_callable(provider: Any) -> "Callable[..., Any] | None":
    """Return the underlying callable of a registry entry, or None if opaque.

    Litestar `Provide` objects expose the actual callable via `.dependency`.
    Some registry entries may already be bare callables (test doubles, edge
    cases). Anything else we can't introspect is treated as having no deps.
    """
    if hasattr(provider, "dependency"):
        return provider.dependency  # type: ignore[no-any-return]
    if callable(provider):
        return provider
    return None


def _collect_consumed_dependencies(
    fn: "Callable[..., Any]",
    registry: "dict[str, Any]",
) -> "set[str]":
    """Return the transitive closure of dependency names consumed by fn.

    Walks fn's signature parameters; for each parameter name that exists in
    `registry`, adds it to the consumed set and recursively walks the
    provider's own signature. Parameters not in the registry are ignored
    (they are tool arguments, `self`, or reserved framework parameters —
    the caller handles those separately).

    Cycles are broken by tracking visited names.
    """
    consumed: set[str] = set()
    stack: list[Callable[..., Any]] = [fn]
    visited_callables: set[int] = set()

    while stack:
        current = stack.pop()
        if id(current) in visited_callables:
            continue
        visited_callables.add(id(current))

        try:
            params = inspect.signature(current).parameters
        except (TypeError, ValueError):
            # Builtins or C-implemented callables may not be introspectable.
            continue

        for param_name in params:
            if param_name in consumed:
                continue
            if param_name not in registry:
                continue
            consumed.add(param_name)
            provider_fn = _provider_callable(registry[param_name])
            if provider_fn is not None:
                stack.append(provider_fn)

    return consumed
```

- [ ] **Step 2a: Extend the reserved-name set in `execute_tool`**

In `litestar_mcp/executor.py`, find the line in `execute_tool` that reads:

```python
unsupported_cli_deps = {"request", "socket", "headers", "cookies", "query", "body"}
```

and replace it with:

```python
unsupported_cli_deps = {
    "request",
    "socket",
    "headers",
    "cookies",
    "query",
    "body",
    "state",
    "scope",
}
```

These two additions (`state`, `scope`) match Litestar's framework-reserved parameter names and prevent future plugins that expose `state` or `scope` as deps from repeating the #19 bug pattern.

- [ ] **Step 2b: Modify `_resolve_dependencies` to honor the consumed set**

Replace the existing `_resolve_dependencies` function in `litestar_mcp/executor.py` with:

```python
async def _resolve_dependencies(
    handler: BaseRouteHandler,
    fn: Any,
    unsupported_cli_deps: "set[str]",
) -> "dict[str, Any]":
    """Resolve only the dependencies the handler transitively consumes.

    Mirrors Litestar's DI semantics: walks the handler signature outward,
    calling only providers whose names appear (directly or transitively)
    in the consumed closure. Providers are resolved dependency-first so
    that a provider needing `foo` receives an already-resolved `foo` kwarg.
    """
    try:
        registry = handler.resolve_dependencies()
    except (AttributeError, TypeError) as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.debug("Failed to resolve dependencies for %s: %s", get_name(fn), e)
        return {}

    consumed = _collect_consumed_dependencies(fn, registry)

    # Reserved-name check runs against the CONSUMED set only.
    for dep_name in consumed:
        _check_unsupported_dependency(dep_name, unsupported_cli_deps, fn)

    # Topological resolution: repeatedly resolve any consumed provider whose
    # own kwargs are either already resolved, not in the registry (tool args
    # or framework params we can't satisfy), or have defaults.
    resolved: dict[str, Any] = {}
    remaining = set(consumed)

    while remaining:
        progress = False
        for dep_name in list(remaining):
            provider = registry[dep_name]
            provider_fn = _provider_callable(provider)
            if provider_fn is None:
                raise NotCallableInCLIContextError(get_name(fn), dep_name)

            try:
                provider_sig = inspect.signature(provider_fn)
            except (TypeError, ValueError):
                provider_sig = None

            kwargs: dict[str, Any] = {}
            ready = True
            if provider_sig is not None:
                for p_name, p in provider_sig.parameters.items():
                    if p_name in resolved:
                        kwargs[p_name] = resolved[p_name]
                    elif p_name in registry and p_name in consumed:
                        # Dependency on another consumed provider — wait.
                        ready = False
                        break
                    elif p.default is not inspect.Parameter.empty:
                        continue
                    else:
                        # Unresolvable kwarg with no default — this provider
                        # genuinely needs request context we don't have.
                        raise NotCallableInCLIContextError(get_name(fn), dep_name)

            if not ready:
                continue

            try:
                if inspect.iscoroutinefunction(provider_fn):
                    resolved[dep_name] = await provider_fn(**kwargs)
                else:
                    resolved[dep_name] = provider_fn(**kwargs)
            except NotCallableInCLIContextError:
                raise
            except Exception as e:
                raise NotCallableInCLIContextError(get_name(fn), dep_name) from e

            remaining.discard(dep_name)
            progress = True

        if not progress:
            # Cycle or unsatisfiable graph — fail with the first stuck dep.
            stuck = next(iter(remaining))
            raise NotCallableInCLIContextError(get_name(fn), stuck)

    return resolved
```

- [ ] **Step 2c: Renumber is not needed — continue**

(Steps 2a and 2b replaced the previous single "Step 2". The Step 3/4/5 numbering below is unchanged.)

- [ ] **Step 3: Run Task 1 and Task 2 tests**

Run:
```
uv run pytest tests/test_executor.py::TestExecutor::test_unused_plugin_dependency_is_not_resolved tests/test_executor.py::TestExecutor::test_unused_plugin_dependency_with_handler_args -v
```

Expected: **both PASS**.

- [ ] **Step 4: Run the full executor test module to catch regressions**

Run: `uv run pytest tests/test_executor.py -v`

Expected: all tests pass. Pay attention to:
- `test_cli_context_limitation_request_dependency` — still passes because the mocked `resolve_dependencies` returns `{"request": AsyncMock()}` and the test handler declares `request` as a parameter, so `request` is in the consumed set and triggers the reserved-name check.
- `test_dependency_resolution_failure` — still passes because the handler declares `config` as a parameter, so the failing provider is in the consumed set and raises.
- `test_execute_tool_with_request_dependency_error` — still passes because the handler declares `request` directly.
- `test_execute_handler_with_dependencies` — still passes because `handler_with_deps` declares `config`.

If any existing test fails, re-read its setup: the most likely cause is a handler that does NOT declare the dep as a parameter but expects it to still be resolved. That would be a change in observable behavior, and per the spec it is intentional — but double-check the test's intent before deciding whether to update the test or the implementation.

- [ ] **Step 5: Commit**

```bash
git add litestar_mcp/executor.py
git commit -m "fix(executor): resolve only consumed deps, skipping plugin extras (#19)"
```

---

## Task 4: Red — transitive dependency resolution

**Files:**
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write the failing test**

Append to `class TestExecutor`:

```python
@pytest.mark.asyncio
async def test_transitive_dependency_resolution(self) -> None:
    """A handler consuming `user` transitively pulls in `role`.

    Verifies the executor walks provider signatures, not just the handler's.
    """
    from litestar import Litestar, get
    from litestar.di import Provide

    from tests.conftest import get_handler_from_app

    def provide_role() -> str:
        return "admin"

    def provide_user(role: str) -> dict[str, str]:
        return {"name": "Alice", "role": role}

    @get("/me", sync_to_thread=False)
    def me(user: dict[str, str]) -> dict[str, str]:
        return user

    app = Litestar(
        route_handlers=[me],
        dependencies={
            "role": Provide(provide_role, sync_to_thread=False),
            "user": Provide(provide_user, sync_to_thread=False),
        },
    )
    handler = get_handler_from_app(app, "/me", "GET")

    result = await execute_tool(handler, app, {})
    assert result == {"name": "Alice", "role": "admin"}
```

- [ ] **Step 2: Run test to verify it passes or fails**

Run: `uv run pytest tests/test_executor.py::TestExecutor::test_transitive_dependency_resolution -v`

Expected: **PASS** (the Task 3 implementation already handles topological resolution). If it fails, the most likely cause is ordering — verify that `provide_role` is resolved before `provide_user` in `_resolve_dependencies` (the `ready = False` branch should defer `user` until `role` is in `resolved`).

If it genuinely passes on the first run, that's expected and good: this test guards the invariant going forward. Proceed to commit.

- [ ] **Step 3: Commit**

```bash
git add tests/test_executor.py
git commit -m "test(executor): verify transitive dep resolution via signature walking"
```

---

## Task 5: Red → Green — clearer error for consumed request-scoped deps

**Files:**
- Modify: `tests/test_executor.py`
- Modify: `litestar_mcp/executor.py`

- [ ] **Step 1: Write the failing test**

Append to `class TestExecutor`:

```python
@pytest.mark.asyncio
async def test_consumed_request_scoped_dep_raises_clear_error(self) -> None:
    """When a handler actually declares a request-scoped dep, the error
    must name the dep and mention request context."""
    from litestar import Litestar, get
    from litestar.di import Provide

    from tests.conftest import get_handler_from_app

    def broken_db_session() -> str:
        msg = "needs a real request scope"
        raise RuntimeError(msg)

    @get("/items", sync_to_thread=False)
    def list_items(db_session: str) -> dict[str, str]:
        return {"db": db_session}

    app = Litestar(
        route_handlers=[list_items],
        dependencies={"db_session": Provide(broken_db_session, sync_to_thread=False)},
    )
    handler = get_handler_from_app(app, "/items", "GET")

    with pytest.raises(NotCallableInCLIContextError) as exc_info:
        await execute_tool(handler, app, {})

    message = str(exc_info.value)
    assert "db_session" in message
    assert "request context" in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_executor.py::TestExecutor::test_consumed_request_scoped_dep_raises_clear_error -v`

Expected: **FAIL** — the assertion `"request context" in message` fails because the current `NotCallableInCLIContextError` message uses the phrase "CLI context".

- [ ] **Step 3: Update the exception message template**

In `litestar_mcp/executor.py`, replace the `NotCallableInCLIContextError.__init__` body:

```python
class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a tool is not callable via MCP due to its dependencies."""

    def __init__(self, handler_name: str, parameter_name: str) -> None:
        """Initialize the exception.

        Args:
            handler_name: Name of the handler that cannot be called.
            parameter_name: Name of the parameter causing the issue.
        """
        super().__init__(
            f"Tool '{handler_name}' cannot be executed via MCP: its dependency "
            f"'{parameter_name}' requires request context (e.g. a plugin-registered "
            f"session, request, or connection-scoped resource). Tools that need "
            f"such resources are not currently MCP-callable."
        )
```

- [ ] **Step 4: Run the new test and the existing error tests**

Run:
```
uv run pytest tests/test_executor.py::TestExecutor::test_consumed_request_scoped_dep_raises_clear_error tests/test_executor.py::TestNotCallableInCLIContextError tests/test_executor.py::TestExecutor::test_cli_context_limitation_request_dependency tests/test_executor.py::TestExecutor::test_execute_tool_with_request_dependency_error tests/test_executor.py::TestExecutor::test_dependency_resolution_failure -v
```

Expected: **all PASS**. The existing `TestNotCallableInCLIContextError` tests only assert that `param_name`, `param_type`, and the substring `"CLI context"` appear in the message. The new message still contains "MCP" and will break the `"CLI context" in str(error)` assertion.

**If `TestNotCallableInCLIContextError.test_not_callable_in_cli_context_error_creation` fails** because of the `"CLI context"` substring check, update that test and the related parametric test. They exist purely to lock the old message text and must be adjusted to the new message:

In `class TestNotCallableInCLIContextError` at the bottom of `tests/test_executor.py`, replace both test methods with:

```python
def test_not_callable_in_cli_context_error_creation(self) -> None:
    """Test creation of NotCallableInCLIContextError."""
    handler_name = "list_items"
    param_name = "db_session"

    error = NotCallableInCLIContextError(handler_name, param_name)

    assert handler_name in str(error)
    assert param_name in str(error)
    assert "request context" in str(error)

def test_not_callable_in_cli_context_error_with_different_types(self) -> None:
    """Test NotCallableInCLIContextError with different parameter types."""
    test_cases = [
        ("list_items", "db_session"),
        ("get_user", "current_user"),
        ("health", "connection"),
    ]

    for handler_name, param_name in test_cases:
        error = NotCallableInCLIContextError(handler_name, param_name)
        assert handler_name in str(error)
        assert param_name in str(error)
        assert "request context" in str(error)
```

Note the parameter semantics: the constructor takes `(handler_name, parameter_name)`, not `(param_name, param_type)`. The old tests mislabeled the second argument — the new ones use accurate names.

Re-run the same pytest command. All should now PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_executor.py litestar_mcp/executor.py
git commit -m "fix(executor): clarify error message for consumed request-scoped deps"
```

---

## Task 6: Red → Green — cyclic dependency does not infinite-loop

**Files:**
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write the test**

Append to `class TestExecutor`:

```python
@pytest.mark.asyncio
async def test_cyclic_dependency_does_not_infinite_loop(self) -> None:
    """Pathological: two providers declare each other as kwargs.

    The visited-set in _collect_consumed_dependencies must break the cycle
    on the signature walk, and the topological loop in _resolve_dependencies
    must detect that neither dep can make progress and raise — not hang.
    """
    from litestar import Litestar, get
    from litestar.di import Provide

    from tests.conftest import get_handler_from_app

    def provide_a(b: str) -> str:
        return f"a({b})"

    def provide_b(a: str) -> str:
        return f"b({a})"

    @get("/cycle", sync_to_thread=False)
    def cycle_handler(a: str) -> dict[str, str]:
        return {"a": a}

    app = Litestar(
        route_handlers=[cycle_handler],
        dependencies={
            "a": Provide(provide_a, sync_to_thread=False),
            "b": Provide(provide_b, sync_to_thread=False),
        },
    )
    handler = get_handler_from_app(app, "/cycle", "GET")

    with pytest.raises(NotCallableInCLIContextError):
        await execute_tool(handler, app, {})
```

- [ ] **Step 2: Run test to verify it passes without hanging**

Run (with a safety timeout so a regression can't hang CI):
```
uv run pytest tests/test_executor.py::TestExecutor::test_cyclic_dependency_does_not_infinite_loop -v --timeout=5
```

Expected: **PASS** within well under 5 seconds. The Task 3 implementation already has the `if not progress:` branch that raises on stuck graphs, and `_collect_consumed_dependencies` tracks visited callables.

If the project does not have `pytest-timeout` installed, drop the `--timeout=5` flag and watch the terminal — the test should finish in <1s. If it hangs, you have a bug in the `remaining` / `progress` loop in `_resolve_dependencies`; re-read Task 3 Step 2 and verify the `if not progress:` branch is present.

- [ ] **Step 3: Commit**

```bash
git add tests/test_executor.py
git commit -m "test(executor): guard against cyclic dependency resolution hangs"
```

---

## Task 7: Verify the issue #19 reproduction end-to-end

**Files:** none modified. This is a manual verification using the script from the issue.

- [ ] **Step 1: Save the reproduction script**

Save this to `/tmp/repro_issue_19.py` (note: use your project's current version, not the pinned 0.3.0 in the issue, so the fix is exercised):

```python
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "litestar>=2.0.0",
#   "advanced-alchemy[uuid]>=1.9.0",
#   "aiosqlite>=0.20.0",
#   "httpx>=0.27.0",
# ]
# ///
"""Reproduce: SQLAlchemy plugin breaks ALL MCP tool execution."""

import asyncio
import json

from advanced_alchemy.base import UUIDAuditBase
from advanced_alchemy.extensions.litestar import SQLAlchemyAsyncConfig, SQLAlchemyPlugin
from httpx import ASGITransport, AsyncClient
from litestar import Litestar, get
from litestar.openapi.config import OpenAPIConfig
from litestar_mcp import LitestarMCP, MCPConfig
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


class ItemModel(UUIDAuditBase):
    __tablename__ = "items"
    name: Mapped[str] = mapped_column(String(100))


@get("/health", mcp_tool="health_check")
async def health_check() -> dict:
    return {"status": "ok"}


app = Litestar(
    route_handlers=[health_check],
    plugins=[
        LitestarMCP(MCPConfig(name="Repro")),
        SQLAlchemyPlugin(
            config=SQLAlchemyAsyncConfig(
                connection_string="sqlite+aiosqlite:///",
                create_all=True,
            )
        ),
    ],
    openapi_config=OpenAPIConfig(title="Repro", version="0.1.0"),
)


async def main() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        http_resp = await client.get("/health")
        print(f"HTTP GET /health -> {http_resp.status_code}: {http_resp.json()}")

        init_resp = await client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test"},
                },
            },
            headers={"Content-Type": "application/json"},
        )
        session_id = init_resp.headers.get("mcp-session-id", "")

        tool_resp = await client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "health_check", "arguments": {}},
            },
            headers={
                "Content-Type": "application/json",
                "mcp-session-id": session_id,
            },
        )
        print("MCP tools/call 'health_check' ->")
        print(json.dumps(tool_resp.json(), indent=2))


asyncio.run(main())
```

- [ ] **Step 2: Run it against the current working tree**

From the project root:
```
uv run --with "advanced-alchemy[uuid]>=1.9.0" --with "aiosqlite>=0.20.0" --with "httpx>=0.27.0" --with-editable . python /tmp/repro_issue_19.py
```

Expected output:
```
HTTP GET /health -> 200: {'status': 'ok'}
MCP tools/call 'health_check' ->
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    ...
    "content": [{"type": "text", "text": "{\"status\": \"ok\"}"}],
    "isError": false
  }
}
```

Key things to verify:
- `tool_resp` contains a `result` key (not `error`).
- `isError` is `false`.
- No mention of `db_engine` or `NotCallableInCLIContext` anywhere in the output.

If the response still contains `"error"`, the fix is incomplete. Re-read Task 3 Step 2 and check that `_collect_consumed_dependencies` is actually called and the result is respected.

- [ ] **Step 3: No commit for this task**

This task is verification only. Delete `/tmp/repro_issue_19.py` or leave it — it's not checked in.

---

## Task 8: Run the full test suite and update the changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`

Expected: **all tests pass**. If anything fails, stop and diagnose before touching the changelog.

- [ ] **Step 2: Open the changelog and find the right section**

Read `CHANGELOG.md`. It is small (~1KB). Locate the most recent unreleased / next-version section, or add an `## [Unreleased]` section at the top if one does not exist, following the format of the most recent released section.

- [ ] **Step 3: Add the fix entry**

Add under a `### Fixed` subheading:

```markdown
### Fixed

- Plugin-registered dependencies (e.g. `SQLAlchemyPlugin`'s `db_engine` / `db_session`) no longer break MCP tool execution for handlers that do not consume them. The executor now resolves only the transitive closure of dependencies actually declared by the handler's signature, mirroring Litestar's own DI semantics. ([#19](https://github.com/cofin/litestar-mcp/issues/19))
```

If `### Fixed` already exists in the current section, append the bullet to it; otherwise add the subheading.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note plugin dep injection fix (#19)"
```

- [ ] **Step 5: Final sanity check**

Run: `uv run pytest -q && git log --oneline -10`

Expected: tests pass, and the last ~6 commits show the TDD progression (failing test → failing test → fix → transitive test → clearer error → cycle test → changelog).

---

## Acceptance Checklist

When every task is complete, verify:

- [ ] `uv run pytest -q` passes.
- [ ] `tests/test_executor.py` has six new tests corresponding to the spec test plan.
- [ ] The reproduction script from issue #19 (Task 7) runs successfully and returns `{"status": "ok"}` via MCP.
- [ ] `NotCallableInCLIContextError` message mentions "request context" for consumed-but-unresolvable deps.
- [ ] `CHANGELOG.md` has a `### Fixed` entry referencing #19.
- [ ] No public API changes (no new exports, no signature changes to `execute_tool` or `NotCallableInCLIContextError`).
- [ ] Git log shows small focused commits, one per task.
