# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
