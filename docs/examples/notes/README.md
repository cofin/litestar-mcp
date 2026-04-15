---
orphan: true
---

# Reference notes examples

A tiny "notes" application implemented across a matrix of backend and
authentication choices so readers can see the **same MCP surface** under
different integration stacks. Every variant exposes the same tools and
resources:

| Tool         | Resource       |
| ------------ | -------------- |
| `list_notes` | `notes_schema` |
| `create_note`| `app_info`     |
| `delete_note`|                |

The shared wire contract lives in
[`docs/examples/notes/shared/contracts.py`](shared/contracts.py) and the
shared bearer/IAP auth helpers live in
[`docs/examples/notes/shared/auth.py`](shared/auth.py). Each variant is a
self-contained `create_app(...)` module — the only differences between
files are the backend (Advanced Alchemy vs SQLSpec), the auth mode
(none / app-managed JWT / Cloud Run JWT / Google IAP), and whether
dependency injection is Litestar-native or routed through Dishka.

## Variants — choose one

| Backend           | Auth                 | DI     | Deployment target | File                                                                                     |
| ----------------- | -------------------- | ------ | ----------------- | ---------------------------------------------------------------------------------------- |
| Advanced Alchemy  | none                 | Litestar | local demo        | [`advanced_alchemy/no_auth.py`](advanced_alchemy/no_auth.py)                             |
| Advanced Alchemy  | none                 | Dishka | local demo        | [`advanced_alchemy/no_auth_dishka.py`](advanced_alchemy/no_auth_dishka.py)               |
| Advanced Alchemy  | OAuth2 JWT (HS256)   | Litestar | local / any ASGI  | [`advanced_alchemy/jwt_auth.py`](advanced_alchemy/jwt_auth.py)                           |
| Advanced Alchemy  | OAuth2 JWT (HS256)   | Dishka | local / any ASGI  | [`advanced_alchemy/jwt_auth_dishka.py`](advanced_alchemy/jwt_auth_dishka.py)             |
| SQLSpec           | none                 | Litestar | local demo        | [`sqlspec/no_auth.py`](sqlspec/no_auth.py)                                               |
| SQLSpec           | none                 | Dishka | local demo        | [`sqlspec/no_auth_dishka.py`](sqlspec/no_auth_dishka.py)                                 |
| SQLSpec           | OAuth2 JWT (HS256)   | Litestar | local / any ASGI  | [`sqlspec/jwt_auth.py`](sqlspec/jwt_auth.py)                                             |
| SQLSpec           | OAuth2 JWT (HS256)   | Dishka | local / any ASGI  | [`sqlspec/jwt_auth_dishka.py`](sqlspec/jwt_auth_dishka.py)                               |
| SQLSpec           | OAuth2 JWT (HS256)   | Litestar | Google Cloud Run  | [`sqlspec/cloud_run_jwt.py`](sqlspec/cloud_run_jwt.py)                                   |
| SQLSpec           | Google IAP (ES256)   | Litestar | Cloud Run + IAP   | [`sqlspec/google_iap.py`](sqlspec/google_iap.py)                                         |

### Auth mode cheat-sheet

- **no-auth** — public demo, no identity scoping. Use for the fastest
  possible local walkthrough.
- **JWT (HS256)** — ordinary application-managed bearer auth. The app
  owns the login endpoint and signs tokens itself.
- **Cloud Run JWT** — same auth model as plain JWT, but with env-driven
  configuration, a public `/healthz`, and a Cloud Run-ready Dockerfile.
  This is **not** a Google IAP example.
- **Google IAP** — identity is managed by Google at the proxy layer.
  The app only validates the signed `x-goog-iap-jwt-assertion` header
  against Google's JWKS.

## Run any variant with one command

Every variant ships a :pep:`723` inline script metadata block, so
`uv` reads its dependencies directly from the file and provisions an
ephemeral environment on first run. No clone, no `uv sync`, no extras
juggling:

```bash
uv run docs/examples/notes/advanced_alchemy/no_auth.py
uv run docs/examples/notes/advanced_alchemy/jwt_auth.py
uv run docs/examples/notes/sqlspec/no_auth.py
uv run docs/examples/notes/sqlspec/google_iap.py
```

See the [single-file run reference](../../usage/uvx_guide.rst) for the
full variant matrix and common pitfalls.

## Multi-replica deployments

Each MCP session is bound to the replica that issued its
`Mcp-Session-Id`. SSE streams pin to that replica because event queues
live in process memory. For Cloud Run / GKE, configure session
affinity on the `Mcp-Session-Id` header and pair it with a shared
session store so stateless POST tool calls can hit any replica. See
[`docs/usage/deployment.rst`](../../usage/deployment.rst) for the full
note.

## Shared contract

The msgspec structs in `shared/contracts.py` (`Note`, `AppInfo`, and
friends) are the canonical shapes exchanged by every variant. The
matching auth helpers (`mint_hs256_token`, `build_oauth_backend`,
`build_login_controller`, `build_iap_token_validator`, and the IAP
header-alias middleware) live in `shared/auth.py`. Treat both modules
as read-only reference — new integrations should reuse them, not
re-implement them.

## See also

- [Advanced Alchemy family README](advanced_alchemy/README.md)
- [SQLSpec family README](sqlspec/README.md)
- [Reference examples usage page](../../usage/reference_examples.rst)
- [`uvx` reference guide](../../usage/uvx_guide.rst)
- Foundation spec: `.agents/specs/reference-notes-foundation/spec.md`
