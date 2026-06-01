# fixme.md — First-class persistent storage with driver-level migrations

**Status:** Plan — not started.
**Owner:** @cofin
**Source research:** sqlspec/.agents/research/research_20260426_litestar_mcp/research.md
**Drives:** litestar-mcp 0.6.0 (target).

---

## 1. Goal

Give litestar-mcp a **first-class persistent storage tier** for the three things that today are either opaque blobs or in-memory only — sessions, tasks, SSE event log — using the **exact same delegate-to-store-class migration pattern** sqlspec uses for its `litestar`, `adk`, and `events` extensions.

End state:

1. The plugin still works out-of-the-box on `MemoryStore()` (no behaviour regression).
2. When the user opts in (`MCPConfig(session_store=AsyncpgMCPStore(...))` etc.) they get **typed, queryable, sweepable, dialect-optimised** tables — not opaque key/value blobs.
3. Schema lives behind `sqlspec/extensions/<adapter>/.../_get_create_*_sql()` helpers — every supported driver carries its own DDL, indexes, and sweep semantics.
4. `migration_config={"include_extensions": ["litestar_mcp"]}` auto-discovers and applies the migration on every supported sqlspec adapter.

## 2. Non-goals

- **Don't break the `Store` protocol path.** `MCPSessionManager` keeps working on `MemoryStore`, `RedisStore`, sqlspec's existing `BaseSQLSpecStore`, and the SQLAlchemy Store. The new typed stores are an *upgrade path*, not a replacement.
- **Don't ship a generic Redis/SQLAlchemy backend in this round.** This work is sqlspec-specific because that's the migration system we're inheriting. Redis / SQLAlchemy / advanced_alchemy can follow once the contract is proven.
- **Don't move existing in-memory components to disk.** `SSEManager`'s queues stay in-process; only the *Last-Event-ID replay log* gains a durable backing.
- **Don't cross the `litestar-mcp` ↔ `sqlspec` runtime dependency boundary.** sqlspec stays an *optional extra* on litestar-mcp; litestar-mcp stays an *optional extra* on sqlspec.

## 3. Architecture decision

### 3.1 Where things live

We keep contracts on the litestar-mcp side and implementations on the sqlspec side, mirroring how `sqlspec.extensions.litestar.BaseSQLSpecStore` already implements `litestar.stores.base.Store`:

| Concern | Repo | Module |
|---|---|---|
| Abstract MCP store contracts (session, task, sse_event) | **litestar-mcp** | `litestar_mcp/stores/{base,session,task,event}.py` |
| sqlspec-flavored abstract bases (per-extension common) | sqlspec | `sqlspec/extensions/litestar_mcp/{store,_types}.py` |
| Per-driver concrete stores | sqlspec | `sqlspec/adapters/<adapter>/litestar_mcp/store.py` |
| Migration delegate file | sqlspec | `sqlspec/extensions/litestar_mcp/migrations/0001_create_litestar_mcp_tables.py` |

### 3.2 Why split this way

- **litestar-mcp owns what the protocol means** (e.g. "a task has these fields, this lifecycle"). That's a feature concern, not a database concern.
- **sqlspec owns dialect-specific DDL, indexes, sweep SQL, and migration discovery.** It already does this for `litestar` / `adk` / `events` and the auto-discovery code at `sqlspec/migrations/base.py:666` is hardcoded to `sqlspec.extensions.<name>`. Rather than fight that for v0.6.0, we **co-locate with the existing extensions** so users get auto-discovery for free.
- A future sqlspec change to allow **third-party migration packages via entry points** (see §11 open question) would let us collapse the sqlspec-side directory into `litestar_mcp/contrib/sqlspec/` if we want — but the contracts in litestar-mcp would not move.

### 3.3 Naming

Throughout, "MCP store" = the new typed store; "Store" = `litestar.stores.base.Store` (the existing key/value protocol). Concrete classes are named `<Adapter>MCPSessionStore`, `<Adapter>MCPTaskStore`, `<Adapter>MCPEventStore`.

## 4. Tables we own

Every table is owned by litestar-mcp's MCP-store layer. Names are configurable via `extension_config["litestar_mcp"]`. Defaults below.

### 4.1 `mcp_sessions`

Replaces the opaque blob in `MemoryStore` for users who need observability and SQL queryability over their session population.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PRIMARY KEY | `Mcp-Session-Id` value |
| `protocol_version` | TEXT NOT NULL | from `initialize` |
| `client_info_json` | JSONB / JSON / TEXT | from `initialize.params.clientInfo` |
| `capabilities_json` | JSONB / JSON / TEXT | negotiated capabilities |
| `initialized` | BOOLEAN NOT NULL DEFAULT FALSE | flipped by `notifications/initialized` |
| `created_at` | TIMESTAMPTZ NOT NULL | wall clock |
| `last_activity` | TIMESTAMPTZ NOT NULL | renewed on every manager touch |
| `expires_at` | TIMESTAMPTZ NOT NULL | `last_activity + max_idle_seconds` |

Indexes: `(expires_at)` for sweep; `(last_activity DESC)` for "recent sessions" admin views.

### 4.2 `mcp_tasks`

Replaces `InMemoryTaskStore` for production use.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PRIMARY KEY | task uuid |
| `session_id` | TEXT NOT NULL | FK → `mcp_sessions(id)` ON DELETE CASCADE |
| `owner_id` | TEXT NOT NULL | auth principal `sub` (or `"anonymous"`) |
| `status` | TEXT NOT NULL | `pending` / `running` / `completed` / `failed` / `canceled` |
| `tool_name` | TEXT NOT NULL | which MCP tool produced the task |
| `payload_json` | JSONB / JSON / TEXT | input arguments |
| `result_json` | JSONB / JSON / TEXT | terminal-state output, NULL until set |
| `error_json` | JSONB / JSON / TEXT | terminal-state error envelope, NULL until set |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `updated_at` | TIMESTAMPTZ NOT NULL | bumped on every status change |
| `expires_at` | TIMESTAMPTZ NOT NULL | TTL deadline |

Indexes: `(session_id, status)`, `(owner_id, status)`, `(expires_at)`.

### 4.3 `mcp_sse_events`

Persists the Last-Event-ID replay log so a client reconnecting after a network blip past the in-process buffer can still resume.

| Column | Type | Notes |
|---|---|---|
| `event_id` | TEXT PRIMARY KEY | the SSE `id:` value |
| `session_id` | TEXT NOT NULL | FK → `mcp_sessions(id)` ON DELETE CASCADE |
| `stream_id` | TEXT NOT NULL | originating stream UUID |
| `event_name` | TEXT NOT NULL DEFAULT 'message' | the SSE `event:` value |
| `data` | TEXT NOT NULL | encoded JSON-RPC envelope |
| `seq` | BIGINT NOT NULL | monotonic sequence per stream (drives Last-Event-ID lookup) |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `expires_at` | TIMESTAMPTZ NOT NULL | matches session TTL by default |

Indexes: `(session_id, stream_id, seq)`, `(expires_at)`.

Storage policy: **only events that are notifications (no `id`)** skip the table; everything with a JSON-RPC `id` or a tool-progress event lands here. The in-process queue still serves live subscribers; the table backs reconnect.

## 5. Module layout

### 5.1 litestar-mcp side — contracts only

```
litestar_mcp/
└── stores/
    ├── __init__.py        # re-exports MCPSessionStore, MCPTaskStore, MCPEventStore + records
    ├── base.py            # MCPStore protocol (TTL helpers, table-name validation, common typing)
    ├── session.py         # MCPSessionStore + MCPSessionRecord (TypedDict)
    ├── task.py            # MCPTaskStore + MCPTaskRecord + TaskStatus enum
    └── event.py           # MCPEventStore + MCPEventRecord
```

Each contract module exposes:
- An `ABC` (`MCPSessionStore`, `MCPTaskStore`, `MCPEventStore`).
- A `TypedDict` record type used for inserts/reads.
- Pure helpers (e.g. `compute_expiry`, `validate_table_name`) that backends share.

`MCPSessionManager` gets a constructor overload: alongside `Store`, accept `MCPSessionStore` directly. The two paths converge inside the manager — sessions ride either backing.

`InMemoryTaskStore` becomes the in-memory implementation of `MCPTaskStore`. The plugin's `_task_store` field changes type to `MCPTaskStore | None`. Same for `SSEManager._replay_log: MCPEventStore | None`.

### 5.2 sqlspec side — implementations and migration

```
sqlspec/
├── extensions/
│   └── litestar_mcp/
│       ├── __init__.py
│       ├── _types.py                 # SessionRecord, TaskRecord, EventRecord (TypedDict)
│       ├── store.py                  # BaseAsyncMCPStore + per-table abstract base classes
│       └── migrations/
│           ├── __init__.py
│           └── 0001_create_litestar_mcp_tables.py
└── adapters/
    └── <adapter>/
        └── litestar_mcp/
            ├── __init__.py
            └── store.py              # <Adapter>MCPSessionStore, <Adapter>MCPTaskStore, <Adapter>MCPEventStore
```

The adapter store classes subclass the sqlspec abstract bases AND implement the litestar-mcp `MCPSessionStore` / `MCPTaskStore` / `MCPEventStore` contracts. Two sources of truth, one implementation — exactly the relationship `BaseSQLSpecStore` has with `litestar.stores.base.Store` today.

## 6. Per-driver implementations — tiered rollout

### 6.1 Tier 1 — must ship in 0.6.0

| Adapter | Stores |
|---|---|
| `asyncpg` | `AsyncpgMCPSessionStore`, `AsyncpgMCPTaskStore`, `AsyncpgMCPEventStore` |
| `aiosqlite` | `AiosqliteMCPSessionStore`, `AiosqliteMCPTaskStore`, `AiosqliteMCPEventStore` |

Rationale: covers production (Postgres, JSONB / BRIN-indexed `expires_at` / partial indexes on hot status filters) and dev/embedded (SQLite, FTS not needed but JSON1 helpful).

### 6.2 Tier 2 — follow-up minor (0.6.x)

| Adapter | Notes |
|---|---|
| `psycopg` (sync) | reuse async DDL; sync sweep helpers |
| `oracledb` | JSON storage via `JSON` type ≥23ai, BLOB+JSON_VALUE fallback for ≤19c per the existing ADK capability story |
| `sqlite` (sync stdlib) | mirror aiosqlite DDL |

### 6.3 Tier 3 — opportunistic (community PR)

`psqlpy`, `cockroach_asyncpg`, `cockroach_psycopg`, `asyncmy`, `aiomysql`, `mysqlconnector`, `pymysql`, `duckdb`, `spanner`, `adbc`. None of these are blockers for the recipe; they fall out of the same `_get_create_*_sql()` helpers via the existing per-adapter capability detection.

### 6.4 What every per-driver class must expose

```python
# Mirrors sqlspec/extensions/litestar/store.py:BaseSQLSpecStore
class AsyncpgMCPSessionStore(BaseAsyncMCPSessionStore["AsyncpgConfig"]):
    async def _get_create_table_sql(self) -> str: ...
    def _get_drop_table_sql(self) -> list[str]: ...
    # CRUD
    async def insert(self, record: MCPSessionRecord) -> None: ...
    async def get(self, session_id: str) -> MCPSessionRecord | None: ...
    async def touch(self, session_id: str, *, max_idle_seconds: float) -> None: ...
    async def delete(self, session_id: str) -> None: ...
    async def delete_expired(self, *, now: datetime | None = None) -> int: ...
    # Admin
    async def list_recent(self, *, limit: int = 100) -> list[MCPSessionRecord]: ...
```

Same shape for `MCPTaskStore` (insert / get / list_by_session / list_by_owner / update_status / wait_for_terminal hint via NOTIFY where the dialect supports it / delete_expired) and `MCPEventStore` (append / replay_after / delete_for_session / delete_expired).

## 7. Migrations — single delegate file

Following the pattern in `sqlspec/extensions/adk/migrations/0001_create_adk_tables.py` verbatim:

```
sqlspec/extensions/litestar_mcp/migrations/0001_create_litestar_mcp_tables.py
```

Behaviour:

- `_get_session_store_class(context)` → resolves `<Adapter>MCPSessionStore` from `config.__module__`
- `_get_task_store_class(context)` → resolves `<Adapter>MCPTaskStore`
- `_get_event_store_class(context)` → resolves `<Adapter>MCPEventStore`
- `_is_tasks_enabled(context)` → checks `extension_config["litestar_mcp"]["enable_tasks"]` (default: `True`)
- `_is_sse_replay_enabled(context)` → checks `extension_config["litestar_mcp"]["enable_sse_replay"]` (default: `False` — opt-in because most apps don't need cross-process replay)
- `up()` → `[session DDL, task DDL?, event DDL?]`
- `down()` → reverse order

This intentionally diverges from the ADK migration in **only one way**: tasks are on-by-default (production deployments invariably need them), SSE replay is off-by-default (most deployments don't reconnect across process boundaries). The flags can flip later without schema changes.

## 8. Plugin wiring inside litestar-mcp

`litestar_mcp/config.py` gets three additions:

```python
@dataclass
class MCPConfig:
    ...
    # New: typed MCP-store overrides. When set, MCPSessionManager / task store /
    # SSE replay log use the typed backing instead of the generic Store.
    mcp_session_store: "MCPSessionStore | None" = None
    mcp_task_store: "MCPTaskStore | None" = None
    mcp_event_store: "MCPEventStore | None" = None
```

`litestar_mcp/plugin.py:__init__` resolution order:

1. If `mcp_session_store` set → use it.
2. Else if `session_store` set (legacy `Store` protocol) → use existing `MCPSessionManager`.
3. Else → `MemoryStore()` (current default, unchanged).

Ditto for tasks (`mcp_task_store` ∨ `InMemoryTaskStore`) and SSE replay (`mcp_event_store` ∨ in-memory ring).

## 9. Test matrix

### 9.1 sqlspec side (lives in sqlspec repo)

```
tests/integration/extensions/litestar_mcp/
├── conftest.py
├── test_session_store_asyncpg.py
├── test_session_store_aiosqlite.py
├── test_task_store_asyncpg.py
├── test_task_store_aiosqlite.py
├── test_event_store_asyncpg.py
├── test_event_store_aiosqlite.py
└── test_migration_apply.py            # exercises _discover_extension_migrations
```

Reuse `pytest-databases` for asyncpg and the in-process aiosqlite path.

### 9.2 litestar-mcp side (lives here)

```
tests/integration/sqlspec/
├── conftest.py
├── test_session_manager_with_sqlspec_session_store.py
├── test_task_dispatch_with_sqlspec_task_store.py
└── test_sse_replay_with_sqlspec_event_store.py
```

Tests boot a Litestar app with `LitestarMCP(MCPConfig(mcp_session_store=AsyncpgMCPSessionStore(...)))`, drive an `initialize` → `tools/call` → reconnect-with-Last-Event-ID → `DELETE /mcp` cycle, and assert the rows in each table behave as documented.

`pyproject.toml` test extras: `litestar-mcp[test-sqlspec]` pulls `sqlspec[asyncpg,aiosqlite]>=0.43`, `pytest-databases`.

## 10. Docs (litestar-mcp side)

New: `docs/usage/persistence.rst` covering:

1. The three storage paths (`Store` blob / sqlspec MCP-store / Memory).
2. Decision matrix.
3. One worked example per Tier 1 adapter.
4. Migration story: `migration_config={"include_extensions": ["litestar_mcp"]}` and the boundary statement (sqlspec does not use Alembic; do not point `adk migrate session` here either).
5. Cross-link sqlspec's `docs/extensions/litestar/mcp_session_store.rst` once it exists.

## 11. Open questions / follow-ups

- **Third-party migration packages in sqlspec.** Today `sqlspec/migrations/base.py:666` hardcodes `sqlspec.extensions.<name>`. A small enhancement — accept dotted module paths in `include_extensions`, e.g. `"litestar_mcp.contrib.sqlspec"`, OR wire entry points — would let us collapse the sqlspec-side folder into litestar-mcp once contracts settle. **Decision needed**: ship now under `sqlspec/extensions/litestar_mcp/` (no sqlspec change), or block on the discovery enhancement. Preference: ship now, file a sqlspec ticket for the discovery enhancement and cut over in 0.7.x.
- **Should `mcp_sse_events` ride on the same Store TTL as `mcp_sessions`?** Probably yes (FK CASCADE handles cleanup on session delete). Confirm during implementation.
- **NOTIFY/LISTEN for task status on Postgres.** sqlspec already has Postgres NOTIFY infra (`sqlspec/adapters/asyncpg/`). Use it to push task status changes instead of the current callback model? Out of scope for 0.6.0; track separately.
- **Owner enforcement**. Today `InMemoryTaskStore.get(task_id, owner_id)` raises if owner mismatches. Mirror exactly in the SQL store — never return rows where `owner_id` differs, even if the task exists.
- **Monotonic `seq` for SSE events.** SQLite has no native monotonic per-group counter without locks; use `INSERT ... RETURNING (SELECT COALESCE(MAX(seq),0)+1 FROM mcp_sse_events WHERE session_id=? AND stream_id=?)` or a small per-stream advisory lock. asyncpg can use a sequence per stream, but that's a lot of sequences — prefer the `SELECT COALESCE(MAX...) +1 FOR UPDATE` pattern inside a single transaction. Decide during Tier-1 implementation.
- **Memory Bank parity.** Out of scope (`research_20260426_adk_next2026/research.md` notes Memory Bank is Vertex-managed only). No pull on this fixme.

## 12. Phased plan

### Phase A — Contracts (this repo only)

- [ ] Land `litestar_mcp/stores/{base,session,task,event}.py` — abstract bases, record TypedDicts, table-name validators.
- [ ] Refactor `MCPSessionManager` to accept either `Store` or `MCPSessionStore`.
- [ ] Refactor `InMemoryTaskStore` → `MCPTaskStore` ABC + `InMemoryTaskStore(MCPTaskStore)` impl.
- [ ] Add `MCPEventStore` ABC + `InMemoryEventStore(MCPEventStore)` impl, refactor `SSEManager` to use it for replay.
- [ ] Update `MCPConfig` with `mcp_session_store`, `mcp_task_store`, `mcp_event_store` fields.
- [ ] Plumb through `LitestarMCP.__init__` resolution order.
- [ ] No behaviour change for existing users (full test suite stays green with default config).

### Phase B — sqlspec implementation (sqlspec repo)

- [ ] Open beads epic `sqlspec/.../litestar-mcp-driver-stores`.
- [ ] Land `sqlspec/extensions/litestar_mcp/{__init__.py,_types.py,store.py}` — sqlspec-flavored abstract bases that satisfy the litestar-mcp contracts.
- [ ] Land Tier 1 adapter stores: `sqlspec/adapters/asyncpg/litestar_mcp/store.py`, `sqlspec/adapters/aiosqlite/litestar_mcp/store.py`.
- [ ] Land `sqlspec/extensions/litestar_mcp/migrations/0001_create_litestar_mcp_tables.py` (delegate-to-store pattern).
- [ ] Land integration tests (§9.1).

### Phase C — End-to-end wiring (litestar-mcp repo)

- [ ] Add `[test-sqlspec]` and `[sqlspec]` extras to `pyproject.toml`.
- [ ] Land integration tests (§9.2).
- [ ] Land docs page (§10).
- [ ] Update CHANGELOG: 0.6.0 entry covering the new contracts + sqlspec extras.

### Phase D — Tier 2 + 3 (incremental)

- [ ] psycopg, oracledb, sqlite (Tier 2) — same migration file gains `_get_*_store_class` resolution for these adapter names automatically.
- [ ] Tier 3 community-driven.

### Phase E — Long-tail (post-0.6)

- [ ] sqlspec ticket: discovery enhancement for third-party migration packages → cut over to `litestar_mcp.contrib.sqlspec` co-location in 0.7.x.
- [ ] Postgres NOTIFY/LISTEN task status push.
- [ ] Redis-flavored `MCPSessionStore` / `MCPTaskStore` / `MCPEventStore` reusing the same contracts.

## 13. Cross-repo coordination

- File the sqlspec-side beads epic as a child of `sqlspec-9cp` (Research: litestar-mcp library). The two repos move in lockstep through Phase B/C.
- sqlspec issue **#418** (msgspec rename heuristic) is unrelated to this work but blocks the eventual unvendoring of `litestar_mcp/utils/serialization.py`. Track separately.
- Memory record on the sqlspec side already saved: `litestar-mcp-code-litestar-litestar-mcp-v0-5`. Add a litestar-mcp side `bd remember` once Phase A lands.

## 14. Acceptance criteria for v0.6.0

- All three contracts (session, task, event) are abstract bases with at least one in-memory implementation, exercised by the existing test suite.
- `MCPConfig.mcp_*_store` fields exist, default to `None`, and are honored.
- sqlspec Tier-1 adapter stores ship and apply via `migration_config={"include_extensions": ["litestar_mcp"]}`.
- Integration tests on both sides green on CI.
- Docs page published.
- CHANGELOG updated with a "Storage tier overhaul — typed sqlspec stores + driver migrations" entry naming the new contracts and the upgrade path.
- Zero behaviour change for users on default config.
