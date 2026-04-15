---
orphan: true
---

# SQLSpec notes reference family

This family implements the shared notes contract
(`docs/examples/notes/shared/contracts.py`) on top of SQLSpec's explicit,
typed async adapter API, backed by a file-backed SQLite database
(`sqlspec.adapters.aiosqlite.AiosqliteConfig`). Every variant exposes
exactly the same MCP surface:

| Tool         | Resource       |
| ------------ | -------------- |
| `list_notes` | `notes_schema` |
| `create_note`| `app_info`     |
| `delete_note`|                |

## Variant matrix

| File                   | DI              | Auth        | Notes scoping           |
| ---------------------- | --------------- | ----------- | ----------------------- |
| `no_auth.py`           | Litestar native | none        | shared public demo data |
| `no_auth_dishka.py`    | Dishka          | none        | shared public demo data |
| `jwt_auth.py`          | Litestar native | OAuth2 JWT  | scoped by `sub`         |
| `jwt_auth_dishka.py`   | Dishka          | OAuth2 JWT  | scoped by `sub`         |
| `google_iap.py`        | Litestar native | Google IAP  | scoped by IAP `sub`     |
| `cloud_run_jwt.py`     | Litestar native | OAuth2 JWT  | scoped by `sub`         |

Dishka variants are a pure DI swap: the Litestar auth surface, the note
service behavior, and the public MCP shapes are all identical to the
plain variants. Dishka wires `SQLSpecNoteService` at request scope; the
MCP plugin's `dependency_provider` hook resolves the same service for
tool calls (`ToolExecutionContext` -> Dishka container).

## SQLSpec conventions

`common.py` is the single owner of SQL, row mapping, and adapter
configuration for the family. Variant files never redeclare SQL. The
module demonstrates the core SQLSpec contract:

- **Explicit adapter config** — `AiosqliteConfig` is imported and
  constructed directly; no factory hides the adapter.
- **Parameterized SQL only** — every query uses qmark (`?`) bound
  parameters; no string formatting with user input.
- **Typed result mapping** — `select` / `select_one` calls pass
  `schema_type=NoteRow` so tools return a typed msgspec struct rather
  than a raw tuple.
- **Commit behavior** — the service commits explicitly after writes so
  the behavior is identical whether the call comes via HTTP (Litestar
  lifecycle) or via the MCP dependency provider.

## Running a variant

Each variant exposes a `create_app(...)` factory suitable for
`litestar.testing.TestClient` and for a plain ASGI server. Every
variant file ships a :pep:`723` inline-script metadata block, so `uv`
reads its dependencies from the file itself — no clone or
`uv sync` required:

```bash
uv run docs/examples/notes/sqlspec/no_auth.py
uv run docs/examples/notes/sqlspec/no_auth_dishka.py
uv run docs/examples/notes/sqlspec/jwt_auth.py               # requires TOKEN_SECRET env
uv run docs/examples/notes/sqlspec/jwt_auth_dishka.py
uv run docs/examples/notes/sqlspec/cloud_run_jwt.py
uv run docs/examples/notes/sqlspec/google_iap.py
```

See the [single-file run reference](../../../usage/uvx_guide.rst) for
the full variant matrix.

## Deployment-oriented variants

Two variants keep the same SQLSpec wiring but focus on a deployment shape
rather than a DI or auth experiment:

- `google_iap.py` puts the notes app behind **Google Identity-Aware
  Proxy**. The platform signs an `x-goog-iap-jwt-assertion` header on
  every request; the app only validates it. Use this when Google
  manages authentication upstream of Cloud Run.
- `cloud_run_jwt.py` targets **Google Cloud Run** with ordinary
  application-managed HS256 JWTs — the same flow as `jwt_auth.py`, but
  with env-driven configuration (`CloudRunSettings.from_env()`), an
  unauthenticated `/healthz` liveness route, and a companion
  `Dockerfile.cloud_run` plus `.cloud_run.env.example`. Use this when
  the application owns the login/token story and Cloud Run is only the
  runtime target. **This is *not* a Google IAP example** — pair it with
  `google_iap.py` only if you want to explicitly compare app-managed
  JWT against platform auth.

The JWT variants require a caller-supplied `token_secret` argument. The
defaults for `issuer` and `audience` follow the foundation's locked
values (`http://localhost:8000/auth` and `http://localhost:8000/api`).
A minimal `/auth/login` controller (built via
`shared.auth.build_login_controller`) accepts a `{"username": ..., "password": ...}`
body and returns an HS256-signed access token so the example is
self-contained.

## Multi-Replica Deployment

Each MCP session (identified by the `Mcp-Session-Id` response header
from `initialize`) is bound to the replica that issued it. SSE streams
pin to that replica because their event queues live in process memory.

For Cloud Run, GKE, or any horizontally-scaled deployment:

1. Configure your load balancer for **session affinity on the
   `Mcp-Session-Id` header** — cookie affinity is insufficient because
   MCP clients use headers, not cookies.
2. Use a shared session store: configure
   `MCPConfig(session_store=...)` with Redis, or the SQL-backed store
   from `advanced_alchemy` / `sqlspec`, so session metadata survives
   replica restarts and any replica can resolve a session id for
   stateless POST tool calls.
3. Sticky routing only matters for the GET SSE stream and any POST
   that expects a server-streamed response. Pure POST → POST tool
   flows can land on any replica that reads the shared store.

See [`docs/usage/deployment.rst`](../../../usage/deployment.rst) for
the full rationale and platform-specific notes.

## When to choose SQLSpec over Advanced Alchemy

- Pick **SQLSpec** (this family) when you want explicit, typed SQL with
  minimal ORM semantics and first-class async adapters for Postgres,
  DuckDB, SQLite, and friends. SQLSpec is the base for the focused
  deployment-oriented variants (Google IAP, Cloud Run JWT) that live
  alongside this family in later chapters.
- Pick **Advanced Alchemy** (`docs/examples/notes/advanced_alchemy/`)
  when you want an ORM-flavored service/repository layer, audit columns
  via `UUIDAuditBase`, and SQLAlchemy session lifecycle integration.

## See also

- [Top-level notes README](../README.md) — family chooser + `uvx` quick-start.
- [Advanced Alchemy sibling family](../advanced_alchemy/README.md) —
  same contract with an ORM-flavored service/repository layer.
- [Reference examples usage page](../../../usage/reference_examples.rst)
- [`uvx` reference guide](../../../usage/uvx_guide.rst)
- Foundation spec: `.agents/specs/reference-notes-foundation/spec.md`
- Family spec: `.agents/specs/sqlspec-reference-family/spec.md`
- Auth docs: `docs/usage/auth.rst`
- Shared contract module: `docs/examples/notes/shared/contracts.py`
- Shared auth helpers: `docs/examples/notes/shared/auth.py`
