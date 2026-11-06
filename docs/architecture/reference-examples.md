---
title: Notes Reference Examples Matrix
---

# Notes Reference Examples Matrix

The `docs/examples/notes/` directory ships a complete Notes reference application implemented across a matrix of database backends, dependency injection containers, authentication modes, and cloud deployment targets.

Every variant exposes the exact same MCP surface so you can compare persistence, DI, and security patterns side by side without changing client code:

| Primitive Type | Exposed Name / URI | Description |
| :--- | :--- | :--- |
| **Tool** | `list_notes` | List notes visible to the current caller (public in `no-auth` variants, owner-scoped by JWT/IAP `sub` in authenticated variants) |
| **Tool** | `create_note` | Create a new note and persist it to the backing SQLite store |
| **Tool** | `delete_note` | Delete a note by ID (enforcing owner isolation in authenticated variants) |
| **Resource** | `notes_schema` | Read-only JSON Schema metadata describing the `Note` payload contract |
| **Resource** | `app_info` | Application metadata describing the active backend family, DI mode, and authentication tier |

---

## Pick a Persistence Family

<div class="lm-grid">
  <div class="lm-card">
    <div>
      <h3>Advanced Alchemy Family (<code>advanced_alchemy</code>)</h3>
      <p>Implements the shared Notes contract on top of Advanced Alchemy's SQLAlchemy ORM service/repository layer with <code>UUIDAuditBase</code> audit columns and Litestar session lifecycle management.</p>
      <p><strong>Choose when:</strong> You want an ORM surface, declarative models, audit columns, and SQLAlchemy repository ergonomics.</p>
    </div>
    <a href="https://github.com/cofin/litestar-mcp/blob/main/docs/examples/notes/advanced_alchemy/README.md">Advanced Alchemy Family README &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>SQLSpec Family (<code>sqlspec</code>)</h3>
      <p>Implements the shared Notes contract using SQLSpec's explicit, typed async SQL adapter API (<code>AiosqliteConfig</code>) with parameterized queries and zero-overhead <code>msgspec.Struct</code> row mapping. Also hosts the Cloud Run and Google IAP deployment variants.</p>
      <p><strong>Choose when:</strong> You want explicit SQL, typed result mapping, or cloud deployment blueprints.</p>
    </div>
    <a href="https://github.com/cofin/litestar-mcp/blob/main/docs/examples/notes/sqlspec/README.md">SQLSpec Family README &rarr;</a>
  </div>
</div>

---

## Complete Variant Matrix

The two families cover all 10 runnable PEP 723 modules (`no_auth.py`, `no_auth_dishka.py`, `jwt_auth.py`, `jwt_auth_dishka.py`, `cloud_run_jwt.py`, `cloud_run_jwt_dishka.py`, `google_iap.py`, and `google_iap_dishka.py`):

| Backend Family | Auth Mode | DI Container | Deployment Target | Module Path | Single-File Command |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `advanced_alchemy` | `no-auth` | Litestar `Provide` | Local demo | `docs/examples/notes/advanced_alchemy/no_auth.py` | `uv run docs/examples/notes/advanced_alchemy/no_auth.py` |
| `advanced_alchemy` | `no-auth` | Dishka (`FromDishka`) | Local demo | `docs/examples/notes/advanced_alchemy/no_auth_dishka.py` | `uv run docs/examples/notes/advanced_alchemy/no_auth_dishka.py` |
| `advanced_alchemy` | `JWT` (HS256) | Litestar `Provide` | Local / ASGI | `docs/examples/notes/advanced_alchemy/jwt_auth.py` | `uv run docs/examples/notes/advanced_alchemy/jwt_auth.py` |
| `advanced_alchemy` | `JWT` (HS256) | Dishka (`FromDishka`) | Local / ASGI | `docs/examples/notes/advanced_alchemy/jwt_auth_dishka.py` | `uv run docs/examples/notes/advanced_alchemy/jwt_auth_dishka.py` |
| `sqlspec` | `no-auth` | Litestar `Provide` | Local demo | `docs/examples/notes/sqlspec/no_auth.py` | `uv run docs/examples/notes/sqlspec/no_auth.py` |
| `sqlspec` | `no-auth` | Dishka (`FromDishka`) | Local demo | `docs/examples/notes/sqlspec/no_auth_dishka.py` | `uv run docs/examples/notes/sqlspec/no_auth_dishka.py` |
| `sqlspec` | `JWT` (HS256) | Litestar `Provide` | Local / ASGI | `docs/examples/notes/sqlspec/jwt_auth.py` | `uv run docs/examples/notes/sqlspec/jwt_auth.py` |
| `sqlspec` | `JWT` (HS256) | Dishka (`FromDishka`) | Local / ASGI | `docs/examples/notes/sqlspec/jwt_auth_dishka.py` | `uv run docs/examples/notes/sqlspec/jwt_auth_dishka.py` |
| `sqlspec` | `Cloud Run` (HS256 JWT) | Litestar `Provide` | Google Cloud Run | `docs/examples/notes/sqlspec/cloud_run_jwt.py` | `uv run docs/examples/notes/sqlspec/cloud_run_jwt.py` |
| `sqlspec` | `IAP` (Google ES256) | Litestar `Provide` | Cloud Run + Google IAP | `docs/examples/notes/sqlspec/google_iap.py` | `uv run docs/examples/notes/sqlspec/google_iap.py` |

---

## Authentication Mode Comparison

The reference variants progress through four authentication tiers (`no-auth`, `JWT`, `Cloud Run`, and `IAP`), each demonstrating how `LitestarMCP` inherits the host application's authentication middleware:

| Auth Mode | Identity Source | Login Endpoint | Notes Scoping | Recommended Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **`no-auth`** | None (anonymous public demo) | None | Shared global notes table | Fast local exploration and schema inspection; never deploy to untrusted networks |
| **`JWT`** | Application-signed HS256 bearer token (`OAuth2PasswordBearerAuth`) | `POST /auth/login` | Scoped per user via JWT `sub` claim | Self-contained ASGI deployments where the application manages user login and bearer issuance |
| **`Cloud Run`** | Application-signed HS256 bearer token configured via `CloudRunSettings.from_env()` | `POST /auth/login` + unauthenticated `GET /healthz` | Scoped per user via JWT `sub` claim | Containerized Google Cloud Run deployments (`Dockerfile.cloud_run`) where the app still owns JWT authentication |
| **`IAP`** | Google Identity-Aware Proxy signed `x-goog-iap-jwt-assertion` header (ES256 JWKS) | Managed upstream by Google proxy | Scoped per user via verified IAP `sub` claim | Zero-trust enterprise deployments behind Google Cloud Load Balancer or Cloud Run with IAP enabled |

---

## Shared Contracts and Auth Helpers

All 10 modules share two read-only foundation modules under `docs/examples/notes/shared/`:

1. **`docs/examples/notes/shared/contracts.py`**: Defines the canonical `msgspec.Struct` wire payloads (`Note`, `CreateNoteInput`, `DeleteNoteResult`, `AppInfo`, and `NotesSchema`) returned by every variant.
2. **`docs/examples/notes/shared/auth.py`**: Provides `mint_hs256_token`, `build_oauth_backend`, `build_login_controller`, `build_iap_token_validator`, `IAPAuthenticationMiddleware`, and `build_iap_auth_middleware`.

When Dishka is used (`no_auth_dishka.py` and `jwt_auth_dishka.py`), `setup_dishka` wires the request-scoped note service into the Litestar application. `LitestarMCP` automatically detects Dishka-managed types and strips them from the public MCP tool JSON Schema while resolving them at `tools/call` runtime.

---

## Running Variants with uv run or uvx

Every variant embeds a PEP 723 inline script metadata header so `uv run` provisions an isolated environment automatically:

```bash
uv run docs/examples/notes/sqlspec/no_auth.py
uv run docs/examples/notes/advanced_alchemy/no_auth.py
```

You can also invoke any module directly via `uvx` with explicit `--from litestar-mcp` and `--with` extras:

```bash
uvx --from litestar-mcp --with "litestar-mcp,sqlspec[aiosqlite],aiosqlite" python -m docs.examples.notes.sqlspec.no_auth
uvx --from litestar-mcp --with "litestar-mcp,advanced-alchemy,aiosqlite" python -m docs.examples.notes.advanced_alchemy.no_auth
```

See [Single-File `uvx` & PEP 723 Guide](uvx-and-pep723.md) for remote URL execution, environment variables for JWT and IAP modes, and stdio bridge configuration.
