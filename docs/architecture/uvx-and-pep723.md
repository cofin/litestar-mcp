---
title: Single-File uvx & PEP 723 Guide
---

# Single-File `uvx` & PEP 723 Guide

Every runnable example under `docs/examples/notes/` declares its dependencies inline using a [PEP 723](https://peps.python.org/pep-0723/) `# /// script` metadata block. `uv` reads that block, provisions an ephemeral virtual environment on first run, and launches the Litestar ASGI server on `http://127.0.0.1:8000` with MCP available at `http://127.0.0.1:8000/mcp`.

---

## 1. Run Any Example with `uv run`

From a repository checkout, pass any variant file directly to `uv run` without running `uv sync`:

```bash
uv run docs/examples/notes/sqlspec/no_auth.py
uv run docs/examples/notes/sqlspec/no_auth_dishka.py
uv run docs/examples/notes/sqlspec/jwt_auth.py
uv run docs/examples/notes/sqlspec/jwt_auth_dishka.py
uv run docs/examples/notes/sqlspec/cloud_run_jwt.py
uv run docs/examples/notes/sqlspec/google_iap.py
uv run docs/examples/notes/advanced_alchemy/no_auth.py
uv run docs/examples/notes/advanced_alchemy/no_auth_dishka.py
uv run docs/examples/notes/advanced_alchemy/jwt_auth.py
uv run docs/examples/notes/advanced_alchemy/jwt_auth_dishka.py
```

### Run Directly from a Remote GitHub URL

If you have not cloned the repository, pass the raw GitHub URL to `uv run`. `uv` downloads, caches, and executes the single file with its declared PEP 723 dependencies:

```bash
uv run https://raw.githubusercontent.com/cofin/litestar-mcp/main/docs/examples/notes/sqlspec/no_auth.py
```

---

## 2. Ephemeral Package Execution with `uvx`

When launching a reference module via `uvx`, pass `--from litestar-mcp`, the required backend packages via `--with`, and the `python -m docs.examples.notes.<family>.<variant>` entrypoint:

```bash
uvx --from litestar-mcp --with "litestar-mcp,sqlspec[aiosqlite],aiosqlite" python -m docs.examples.notes.sqlspec.no_auth
uvx --from litestar-mcp --with "litestar-mcp,sqlspec[aiosqlite],aiosqlite,dishka" python -m docs.examples.notes.sqlspec.no_auth_dishka
uvx --from litestar-mcp --with "litestar-mcp,sqlspec[aiosqlite],aiosqlite,pyjwt,cryptography" python -m docs.examples.notes.sqlspec.jwt_auth
uvx --from litestar-mcp --with "litestar-mcp,sqlspec[aiosqlite],aiosqlite,pyjwt,cryptography,dishka" python -m docs.examples.notes.sqlspec.jwt_auth_dishka
uvx --from litestar-mcp --with "litestar-mcp,sqlspec[aiosqlite],aiosqlite,pyjwt,cryptography" python -m docs.examples.notes.sqlspec.cloud_run_jwt
uvx --from litestar-mcp --with "litestar-mcp,sqlspec[aiosqlite],aiosqlite,pyjwt,cryptography" python -m docs.examples.notes.sqlspec.google_iap
uvx --from litestar-mcp --with "litestar-mcp,advanced-alchemy,aiosqlite" python -m docs.examples.notes.advanced_alchemy.no_auth
uvx --from litestar-mcp --with "litestar-mcp,advanced-alchemy,aiosqlite,dishka" python -m docs.examples.notes.advanced_alchemy.no_auth_dishka
uvx --from litestar-mcp --with "litestar-mcp,advanced-alchemy,aiosqlite,pyjwt,cryptography" python -m docs.examples.notes.advanced_alchemy.jwt_auth
uvx --from litestar-mcp --with "litestar-mcp,advanced-alchemy,aiosqlite,pyjwt,cryptography,dishka" python -m docs.examples.notes.advanced_alchemy.jwt_auth_dishka
```

---

## 3. Complete Command Matrix across All 4 Auth Modes

The table below maps both families (`advanced_alchemy` and `sqlspec`) across the 4 authentication modes (`no-auth`, `JWT`, `Cloud Run`, and `IAP`) and all 5 variant patterns (`no_auth.py`, `jwt_auth.py`, Dishka DI `no_auth_dishka.py` / `jwt_auth_dishka.py` / `dishka_di.py`, `cloud_run_jwt.py` / `cloud_run.py`, and `google_iap.py` / `gcp_iap.py`):

| Family / Variant | Auth Mode | Required Environment Variables | Single-File PEP 723 Command |
| :--- | :--- | :--- | :--- |
| `advanced_alchemy/no_auth.py` | `no-auth` | None | `uv run docs/examples/notes/advanced_alchemy/no_auth.py` |
| `advanced_alchemy/no_auth_dishka.py` | `no-auth` (Dishka DI) | None | `uv run docs/examples/notes/advanced_alchemy/no_auth_dishka.py` |
| `advanced_alchemy/jwt_auth.py` | `JWT` (HS256) | `TOKEN_SECRET` (min 32 bytes) | `uv run docs/examples/notes/advanced_alchemy/jwt_auth.py` |
| `advanced_alchemy/jwt_auth_dishka.py` | `JWT` (HS256 + Dishka DI) | `TOKEN_SECRET` (min 32 bytes) | `uv run docs/examples/notes/advanced_alchemy/jwt_auth_dishka.py` |
| `sqlspec/no_auth.py` | `no-auth` | None | `uv run docs/examples/notes/sqlspec/no_auth.py` |
| `sqlspec/no_auth_dishka.py` | `no-auth` (Dishka DI) | None | `uv run docs/examples/notes/sqlspec/no_auth_dishka.py` |
| `sqlspec/jwt_auth.py` | `JWT` (HS256) | `TOKEN_SECRET` (min 32 bytes) | `uv run docs/examples/notes/sqlspec/jwt_auth.py` |
| `sqlspec/jwt_auth_dishka.py` | `JWT` (HS256 + Dishka DI) | `TOKEN_SECRET` (min 32 bytes) | `uv run docs/examples/notes/sqlspec/jwt_auth_dishka.py` |
| `sqlspec/cloud_run_jwt.py` | `Cloud Run` (HS256 JWT) | `TOKEN_SECRET`, optional `PORT` / `DB_PATH` | `uv run docs/examples/notes/sqlspec/cloud_run_jwt.py` |
| `sqlspec/google_iap.py` | `IAP` (Google ES256) | `IAP_AUDIENCE`, optional `IAP_ISSUER` | `uv run docs/examples/notes/sqlspec/google_iap.py` |

---

## 4. Connecting MCP Clients & CLI Bridge

Once a variant is running on `http://127.0.0.1:8000`, point any Streamable HTTP MCP client at `http://127.0.0.1:8000/mcp`:

```json title="claude_desktop_config.json (Streamable HTTP)"
{
  "mcpServers": {
    "notes-demo": {
      "url": "http://127.0.0.1:8000/mcp",
      "transport": "http"
    }
  }
}
```

### Probing the Endpoint with `curl`

Verify the running server responds to `2026-07-28` `server/discover`:

```bash
curl -sX POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}' | jq .
```

### Stdio & Remote HTTP Bridge Commands

For desktop MCP hosts that launch a local stdio subprocess, run either the in-process stdio runner or the HTTP relay bridge from your application environment:

```bash
litestar --app my_app:app mcp stdio
litestar --app my_app:app mcp bridge
litestar mcp bridge --url http://127.0.0.1:8000/mcp
```

---

## 5. Common Pitfalls

!!! warning "JWT variants require `TOKEN_SECRET`"
    The `jwt_auth.py`, `jwt_auth_dishka.py`, and `cloud_run_jwt.py` variants validate HS256 bearer tokens. Set `TOKEN_SECRET` (at least 32 characters for HS256) in your environment before launching, then call `POST /auth/login` with `{"username": "alice", "password": "secret"}` to mint a bearer token.

!!! note "Do not confuse `Cloud Run` JWT with Google `IAP`"
    `cloud_run_jwt.py` uses application-managed HS256 JWT authentication packaged for Google Cloud Run (`Dockerfile.cloud_run`). `google_iap.py` delegates authentication to Google Identity-Aware Proxy upstream of the container and verifies the `x-goog-iap-jwt-assertion` ES256 signature against Google's JWKS.

!!! tip "Production container builds still use `uv sync --frozen`"
    PEP 723 script headers are designed for zero-setup single-file execution and local prototyping. Production container images (such as `Dockerfile.cloud_run`) should install from a locked `uv.lock` via `uv sync --frozen --no-dev`.
