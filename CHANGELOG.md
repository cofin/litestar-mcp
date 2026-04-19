# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- `@mcp_tool` and `@mcp_resource` accept `description`, `agent_instructions`,
  `when_to_use`, and `returns` keyword arguments. The matching Litestar
  `opt`-form keys `mcp_description` / `mcp_resource_description`,
  `mcp_agent_instructions`, `mcp_when_to_use`, and `mcp_returns` are
  recognised on route handlers — use them when declaring MCP tools via
  `@get("/x", opt={...})` rather than the decorators. `tools/list`,
  `resources/list`, `/.well-known/agent-card.json`, and
  `/.well-known/mcp-server.json` render a combined description with
  `## When to use` / `## Returns` / `## Instructions` sections whenever
  any structured field is set. Apps that rely on the default `fn.__doc__`
  behaviour keep their existing output unchanged. CLI output
  (`litestar mcp list-tools` / `list-resources` / `run`) stays plain-text.
  Closes [#39](https://github.com/cofin/litestar-mcp/issues/39).
- `JWKSCache` protocol and `DefaultJWKSCache` implementation for injectable
  JWKS / OIDC discovery caches. `OIDCProviderConfig(jwks_cache=...)` and
  `create_oidc_validator(jwks_cache=...)` accept any `JWKSCache` instance so
  applications can share one document cache across their own auth stack and
  `litestar-mcp`. Closes [#38](https://github.com/cofin/litestar-mcp/issues/38).
- `MCPConfig.opt_keys: MCPOptKeys` lets apps rename any `handler.opt[...]`
  key the plugin reads — tool/resource discovery (`mcp_tool`, `mcp_resource`)
  and the five description-rendering keys introduced above. Pattern
  mirrors first-party Litestar conventions (see
  `litestar.security.jwt.auth.JWTAuth.exclude_opt_key`). Defaults are
  unchanged, so existing apps need no code changes.
- MCP tool invocations now enforce guards from every layer
  (app / router / controller / route) via `handler.resolve_guards()`. Guards
  run before dependency resolution; stdio / CLI mode (no live request) skips
  guard enforcement. `MCPConfig.guards` continues to gate the `/mcp` router
  itself.
- **`litestar_mcp.auth.MCPAuthBackend`** — the built-in
  `AbstractAuthenticationMiddleware` that validates bearer tokens via OIDC
  providers and/or a custom `token_validator` and populates
  `connection.user` / `connection.auth`. Install via
  `DefineMiddleware(MCPAuthBackend, providers=[...], user_resolver=...)`.
  Apps that already ship their own auth middleware (DMA's IAP,
  Litestar's JWT backends, etc.) don't need this — MCP tool handlers
  read `request.user` / `request.auth` populated by whichever middleware
  the app installed.

### Changed

- **Module layout:** `litestar_mcp.auth` is now a package. Public exports
  (`MCPAuthConfig`, `OIDCProviderConfig`, `MCPAuthBackend`,
  `create_oidc_validator`, `TokenValidator`) are unchanged at the package
  level. `litestar_mcp.oidc` moved to `litestar_mcp.auth.oidc`.
- **Core runtime dependencies** expanded to `litestar[jwt]>=2.0.0` and
  `httpx>=0.24.1`. This folds the previous optional `auth` extra into the
  base install — `pip install litestar-mcp` now ships with everything
  required for OIDC validation out of the box.

### Breaking

- **Removed the `auth` optional dependency extra.** OIDC dependencies
  (`httpx`, `litestar[jwt]` which pulls in `pyjwt[crypto]`) are now part of
  the core install. **Migration:** replace `pip install litestar-mcp[auth]`
  with `pip install litestar-mcp`. Consumers who kept `[auth]` in their
  requirements will see a harmless pip warning; remove the suffix at your
  convenience.
- **Removed inline OAuth-scope enforcement in `tools/call`.** `@mcp_tool(scopes=[...])`
  now records scopes as discovery metadata only — they are surfaced under
  `tools[].annotations.scopes` in `tools/list` and in
  `/.well-known/oauth-protected-resource`. MCP tool dispatch runs
  `handler.resolve_guards()`, so authorization for MCP tool calls is driven
  by the same Litestar guards that protect the HTTP route. The previous
  inline check double-enforced on top of guards and produced false rejections
  for auth backends that encode scopes under non-standard claims (`scp`,
  `permissions`, custom). **Migration:** wherever you relied on `scopes=[...]`
  as an enforcement gate, add a Litestar `Guard` to the controller / router /
  route that owns the handler. The guard receives the same `ASGIConnection`
  an HTTP request does, so existing `require_x` guards work unchanged on MCP.
- **Removed `ToolExecutionContext` dataclass and exports from `litestar_mcp/executor.py`.**
  This legacy observability marker is no longer used by the native
  request-pipeline.
- **Removed `attach_stream` and `detach_stream` methods from `SSEManager` in `litestar_mcp/sse.py`.**
  Stream association is now handled automatically within `open_stream`.
- **Removed `MCPConfig.dependency_provider` and the `MCPDependencyProvider`
  protocol (`litestar_mcp/types.py` deleted).** Tool handlers now receive
  dependencies through Litestar's native DI pipeline
  (`create_kwargs_model` → `resolve_dependencies` → `parse_values_from_connection_kwargs`).
  Migrate by declaring dependencies with standard `Provide(...)` on the
  handler / router / app, or with `@inject` + `FromDishka[T]` when using the
  Dishka integration — the same way you would for any other Litestar route.
- **`MCPAuthConfig` collapsed to pure metadata.** Only `issuer`, `audience`,
  and `scopes` survive — these describe the auth surface advertised by
  `/.well-known/oauth-protected-resource`. Removed fields:
  `token_validator`, `user_resolver`, `providers`, `on_validation_error`.
  Migrate enforcement into a Litestar middleware (the new `MCPAuthBackend`
  or your own `AbstractAuthenticationMiddleware`); tool handlers read
  `request.user` / `request.auth`.
- **Removed `MCPAuthHardRejectionError`**, `validate_bearer_token`,
  `resolve_user`, and the bespoke `routes.py:_authenticate_request` path.
  Each middleware now owns its token space; failures raise
  `NotAuthorizedException` and Litestar returns HTTP 401.
- **`initialize`, `ping`, and `notifications/initialized` are no longer
  exempt from auth.** Clients must present a bearer token before any
  JSON-RPC call. The unauthenticated
  `/.well-known/oauth-protected-resource` endpoint remains available for
  discovery clients to bootstrap.
- **`MCPConfig.auth` now accepts only the collapsed metadata struct.**

### Moved

- `litestar_mcp.oidc` → `litestar_mcp.auth.oidc`.
- `litestar_mcp/auth.py` → `litestar_mcp/auth/` package.
- `litestar_mcp/types.py` deleted entirely.

### Migration

**Dependency injection** — replace `MCPConfig(dependency_provider=...)` with
standard Litestar DI on handlers/controllers/app:

```python
# Before
MCPConfig(dependency_provider=my_context_manager)

# After — handler uses Provide() or @inject + FromDishka[T]
MCPConfig()  # no dependency_provider needed
```

**Authentication** — replace `MCPAuthConfig(token_validator=..., user_resolver=...)`
with a Litestar middleware:

```python
# Before
MCPConfig(auth=MCPAuthConfig(token_validator=my_validator, user_resolver=my_resolver))

# After — install MCPAuthBackend as middleware
Litestar(
    middleware=[DefineMiddleware(MCPAuthBackend, token_validator=my_validator, user_resolver=my_resolver)],
    plugins=[LitestarMCP(MCPConfig(auth=MCPAuthConfig(issuer="...", audience="...")))],
)
```

**OIDC providers** — move from `MCPAuthConfig.providers` to `MCPAuthBackend`:

```python
# Before
MCPAuthConfig(providers=[OIDCProviderConfig(issuer="...", audience="...")])

# After
DefineMiddleware(MCPAuthBackend, providers=[OIDCProviderConfig(issuer="...", audience="...")])
```

## v0.4.0 — 2026-04-15

### Added

- `MCPConfig.session_store` — pluggable `litestar.stores.base.Store` for MCP
  session state (defaults to `MemoryStore`). Backends shipped by Litestar
  (`MemoryStore`, `FileStore`, `RedisStore`) and community packages
  (`advanced_alchemy`, `sqlspec`) work out-of-the-box.
- `MCPConfig.dependency_provider` — async context manager hook for injecting
  per-call dependencies into tool handlers.
- `MCPAuthConfig.user_resolver` — post-token-validation hook that resolves
  claims into a user object, injected into handlers.
- `litestar_mcp.create_oidc_validator()` — composable callable factory for
  OIDC token validation, with configurable `clock_skew` and `jwks_cache_ttl`.
  Pairs with the existing declarative `OIDCProviderConfig`.
- `MCPAuthConfig.OIDCProviderConfig` — declarative OIDC/JWKS support with
  auto-discovery from `.well-known/openid-configuration` and in-memory JWKS
  cache.
- `GET /mcp` SSE endpoint with `Last-Event-ID` resumability.
- `DELETE /mcp` for explicit session termination per spec.
- Tool argument validation via `msgspec.convert` against
  `handler.signature_model`; `INVALID_PARAMS` errors include JSON Pointer
  paths in `data.errors`.
- Reference example family under `docs/examples/notes/` (Advanced Alchemy +
  SQLSpec + Dishka + JWT + Google IAP + Cloud Run JWT). Each example is
  single-file runnable via `uv run` thanks to PEP 723 inline metadata.
- CLI (`litestar_mcp/cli.py`), manifest generation
  (`litestar_mcp/manifests.py`), task lifecycle module
  (`litestar_mcp/tasks.py`).
- New `docs/usage/uvx_guide.rst` leading with PEP 723 single-file run.
- Deployment note covering sticky routing on `Mcp-Session-Id` for
  multi-replica deployments (Cloud Run, GKE).
- "Advanced Integration: OIDC" README section with Google IAP and generic
  OIDC examples.
- Regression test `tests/unit/test_version_sync.py` guarding that
  `litestar_mcp.__metadata__.__version__` matches the packaged distribution
  version.
- `MCPAuthConfig.on_validation_error` — observability hook called with
  `(issuer, exception)` on every OIDC validation failure path (JWKS fetch,
  unknown kid, bad alg, expired, bad aud/iss/sig). Sync or async; hook
  exceptions are logged and swallowed so auth outcomes stay independent
  of observability plumbing. Also accepted by
  `create_oidc_validator()`.
- `MCPAuthHardRejectionError` — exported from the package root. Raise from
  a `token_validator` to signal "I own this token and it is invalid" and
  skip OIDC provider fallthrough. Terminal HTTP response remains 401 per
  MCP / OAuth 2.1.
- `MCPDependencyProvider` Protocol in new `litestar_mcp.types` module.
  Runtime-checkable; accepts sync or async context managers returning a
  mapping of injected kwargs.
- Per-URL single-flight locking on the JWKS / OIDC discovery cache.
  Concurrent cold-cache callers for the same URL coalesce into one
  upstream fetch; distinct URLs keep parallel throughput.

### Changed

- **Breaking — Session model.** Sessions are now spec-compliant: a single
  `Mcp-Session-Id` header survives across POST/GET/DELETE independent of any
  SSE stream. The previous collapsed SSE+session model is removed. Existing
  clients that relied on per-stream session creation must call `initialize`
  and reuse the returned session header.
- **Breaking — Tool error envelope.** `INVALID_PARAMS` tool errors return
  `{"error": "...", "errors": [{"path": ..., "message": ...}, ...]}` instead
  of `{"error": "...", "details": [...]}`. The path is a JSON Pointer (e.g.
  `/arguments/age`).
- **Breaking — `MCPSessionManager` API.** The previous internal
  `MCPSessionManager` is replaced by `litestar_mcp.sessions.MCPSessionManager`,
  which takes a `litestar.stores.base.Store`. If you imported the old class
  directly (rather than via `MCPConfig`), update the import.
- Schema generation for msgspec Structs delegates to `msgspec.json.schema()`
  — adds `$defs`, Enum support, `Meta` constraint translation, and
  tagged-union discriminators that the hand-rolled generator omitted.
- All MCP transport JSON encoding/decoding goes through
  `litestar.serialization.encode_json` / `decode_json` for consistency with
  the rest of the Litestar pipeline.
- **Breaking — `MCPConfig.dependency_provider` type.** Narrowed from
  `Any | None` to `MCPDependencyProvider | None`. Existing callables
  continue to work via structural (Protocol) matching; only explicit type
  annotations need updating.
- Claims-dict contract on `MCPAuthConfig.token_validator` is now
  documented: the returned mapping is opaque to litestar-mcp and is passed
  as-is to `user_resolver`. Keys prefixed with `_` are reserved for
  downstream use.

### Removed

- `jsonschema` runtime dependency. Tool argument validation is now driven by
  `msgspec.convert` and `handler.signature_model`.
- The hand-rolled `msgspec_to_json_schema` shim in `schema_builder.py`
  (delegates to `msgspec.json.schema()` now).
- The collapsed-SSE-and-session code paths in `sse.py`.
- `GAPS_FOR_0.4.0.md` gap-analysis document (archived under
  `.agents/archive/v0.4.0-gaps.md`).

## [0.1.0] - 2025-01-04

### Added

- Initial Litestar MCP Plugin implementation
- Support for exposing application routes as MCP resources
- Route validation and dependency analysis tools
- Custom resource and tool handler interfaces
- SQLite integration for advanced examples
- Comprehensive documentation with Sphinx
- CLI interface for plugin management
- Development tools and testing infrastructure

### Features

- **Zero Configuration**: Works out of the box with sensible defaults
- **Custom Handlers**: Extensible interfaces for domain-specific functionality
- **Security Controls**: Configurable access controls and filtering
- **Debug Support**: Built-in debugging and introspection capabilities
- **Type Safety**: Full type hints throughout the codebase
