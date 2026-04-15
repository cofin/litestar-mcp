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
`litestar.testing.TestClient` and for a plain ASGI server. Smoke-test
any variant with:

```bash
uv run python -m docs.examples.notes.sqlspec.no_auth
uv run python -m docs.examples.notes.sqlspec.no_auth_dishka
uv run python -m docs.examples.notes.sqlspec.jwt_auth       # requires token_secret
uv run python -m docs.examples.notes.sqlspec.jwt_auth_dishka
```

The JWT variants require a caller-supplied `token_secret` argument. The
defaults for `issuer` and `audience` follow the foundation's locked
values (`http://localhost:8000/auth` and `http://localhost:8000/api`).
A minimal `/auth/login` controller (built via
`shared.auth.build_login_controller`) accepts a `{"username": ..., "password": ...}`
body and returns an HS256-signed access token so the example is
self-contained.

## When to choose SQLSpec over Advanced Alchemy

- Pick **SQLSpec** (this family) when you want explicit, typed SQL with
  minimal ORM semantics and first-class async adapters for Postgres,
  DuckDB, SQLite, and friends. SQLSpec is the base for the focused
  deployment-oriented variants (Google IAP, Cloud Run JWT) that live
  alongside this family in later chapters.
- Pick **Advanced Alchemy** (`docs/examples/notes/advanced_alchemy/`)
  when you want an ORM-flavored service/repository layer, audit columns
  via `UUIDAuditBase`, and SQLAlchemy session lifecycle integration.

## Cross-references

- Foundation spec: `.agents/specs/reference-notes-foundation/spec.md`
- Family spec: `.agents/specs/sqlspec-reference-family/spec.md`
- Auth docs: `docs/usage/auth.rst`
- Shared contract module: `docs/examples/notes/shared/contracts.py`
- Shared auth helpers: `docs/examples/notes/shared/auth.py`
