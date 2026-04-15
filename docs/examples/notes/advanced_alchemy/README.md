---
orphan: true
---

# Advanced Alchemy notes reference family

This family implements the shared notes contract
(`docs/examples/notes/shared/contracts.py`) on top of Advanced Alchemy's
service/repository layer and a file-backed SQLite database. Every variant
exposes exactly the same MCP surface:

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
plain variants. Dishka wires `NoteService` at request scope; the MCP
plugin's `dependency_provider` hook resolves the same service for tool
calls (`ToolExecutionContext` -> Dishka container).

## Running a variant

Each variant exposes a `create_app(...)` factory suitable for
`litestar.testing.TestClient` and for a plain ASGI server. Every
variant file ships a :pep:`723` inline-script metadata block, so `uv`
reads its dependencies from the file itself — no clone or
`uv sync` required:

```bash
uv run docs/examples/notes/advanced_alchemy/no_auth.py
uv run docs/examples/notes/advanced_alchemy/no_auth_dishka.py
uv run docs/examples/notes/advanced_alchemy/jwt_auth.py        # requires TOKEN_SECRET env
uv run docs/examples/notes/advanced_alchemy/jwt_auth_dishka.py
```

See the [single-file run reference](../../../usage/uvx_guide.rst) for
the full variant matrix.

The JWT variants require a caller-supplied `token_secret` argument. The
defaults for `issuer` and `audience` follow the foundation's locked
values (`http://localhost:8000/auth` and `http://localhost:8000/api`).
A minimal `/auth/login` controller (built via
`shared.auth.build_login_controller`) accepts a `{"username": ..., "password": ...}`
body and returns an HS256-signed access token so the example is
self-contained.

## When to choose AA over SQLSpec

- Pick **Advanced Alchemy** when you want an ORM-flavored service/repository
  layer, audit columns via `UUIDAuditBase`, and SQLAlchemy session lifecycle
  integration with Litestar.
- Pick **SQLSpec** (`docs/examples/notes/sqlspec/`) when you want explicit,
  typed SQL with minimal ORM semantics and first-class async adapters for
  Postgres, DuckDB, and friends.

## See also

- [Top-level notes README](../README.md) — family chooser + `uvx` quick-start.
- [SQLSpec sibling family](../sqlspec/README.md) — same contract with
  explicit SQL, plus Cloud Run JWT and Google IAP deployment variants.
- [Reference examples usage page](../../../usage/reference_examples.rst)
- [`uvx` reference guide](../../../usage/uvx_guide.rst)
- Foundation spec: `.agents/specs/reference-notes-foundation/spec.md`
- Auth docs: `docs/usage/auth.rst`
- Shared contract module: `docs/examples/notes/shared/contracts.py`
- Shared auth helpers: `docs/examples/notes/shared/auth.py`
