# Unified AI Development Bootstrap (Intelligent Edition)

**Version**: 3.0 | **Audience**: Claude Code, Gemini CLI, OpenAI Codex CLI (this environment)

This is the **single source of truth** for how AI agents should plan, implement, test, document, and share context in this repository (and can be copied to other projects).

If another agent-specific file exists (e.g. `CLAUDE.md`, `.gemini/GEMINI.md`), it should **point here** and only contain tool/config specifics.

---

## 0) Prime Directives (All Agents)

1. **Context first, code second**
   - Read existing patterns before writing new code.
   - Prefer extending existing abstractions over inventing new ones.
2. **Pattern-first implementation**
   - Identify 3–5 similar implementations and match their structure, naming, errors, tests, and docs style.
3. **Adaptive complexity**
   - **Simple**: 6 checkpoints (small change, 1–2 files, no new subsystem).
   - **Medium**: 8 checkpoints (new endpoint/service, 2–4 files, new tests).
   - **Complex**: 10+ checkpoints (architecture change, 5+ files, new workflows/integration points).
4. **Knowledge accumulation**
   - Every completed feature leaves behind reusable patterns in `specs/guides/`.
5. **Graceful degradation**
   - Missing tools → fall back to manual structured reasoning, but **never** skip correctness checks.
6. **Truthfulness**
   - Don’t guess: confirm by reading code, running commands, or explicitly asking.

---

## 1) Shared Filesystem Contract (Cross‑Agent Memory)

These locations are shared context across **all** agents and should be used consistently:

### Canonical docs

- `AGENTS.md` (this file): behavioral rules + workflow contract
- `specs/guides/`: detailed, evolving project guides
- `specs/guides/patterns/`: extracted reusable patterns (index in `README.md`)
- `specs/guides/quality-gates.yaml`: quality gates / acceptance checks

### Feature workspaces

Each feature/initiative gets a workspace:

```
specs/active/{slug}/
  README.md
  prd.md
  tasks.md
  recovery.md
  research/
  patterns/
  tmp/
```

Completed workspaces move to `specs/archive/{slug}/`.

### Context Pack (required for handoffs)

Every active workspace must include a short “context pack” **inside** `README.md` (or a `context.md` if preferred) containing:

- Goal + non-goals
- Detected patterns (files + notes)
- Decisions (tradeoffs, constraints)
- Test plan
- Commands used to verify

This makes handoffs deterministic across Claude ↔ Gemini ↔ Codex.

---

## 2) Project Profile (Fill/Update Per Repo)

This section is intended to be **project-specific**. When copying this file to a new repo, update it during `/bootstrap`.

### litestar-mcp (current repo)

- **Purpose**: Litestar plugin integrating the Model Context Protocol (MCP)
- **Language**: Python
- **Build tool**: `uv` + `Makefile`
- **Quality**: ruff + mypy + pyright + slotscheck; tests via pytest; docs via Sphinx
- **Key commands**: see `CLAUDE.md` for authoritative command list

### Code standards (project-specific)

These rules apply to **all agents**, regardless of which tool is used:

- **Type annotations**
  - **Prohibited**: `from __future__ import annotations`
  - **Prohibited**: PEP 604 unions (`T | None`)
  - **Required**: `typing.Optional[T]`, `typing.Union[A, B]`
  - **Required**: stringified non-builtin types (and stringified built-in generics in this repo)
- **Comments**
  - Avoid inline comments in source; use docstrings when commentary is truly needed.

If `CLAUDE.md` conflicts with this file, treat it as a **bug**: align them (this file is canonical for agent behavior; `CLAUDE.md` is canonical for project commands and architecture notes).

---

## 3) Tool Strategy (MCP + Local Tools)

### Tool categories (conceptual)

- **Reasoning**: structured step-by-step breakdown
- **Planning**: multi-phase task decomposition
- **Analysis**: architecture/perf/security review
- **Debug**: root-cause investigation
- **Research**: official docs lookup

### Preferred ordering + fallbacks

1. **Read the repo first**: `rg`, file reads, existing guides
2. **Use deep reasoning for complex work**
   - Claude/Gemini: sequential thinking / planner tools if available
   - Codex: write a short checklist + verify with repo evidence
3. **Use research tools for external docs**
   - Prefer official documentation sources
4. **Always end with validation**
   - Tests, linting, type-checking, doc build when applicable

Repo-specific tool guidance lives in:

- `.claude/mcp-strategy.md` (Claude)
- `.gemini/mcp-strategy.md` (Gemini, if configured)

---

## 4) Unified Workflow (Commands are “conceptual”)

Even if an agent doesn’t support slash commands, it must follow the same phases and produce the same artifacts.

### `/bootstrap` (once per repo; alignment mode supported)

Goal: create/align the agent system so every agent shares the same knowledge.

Outputs:

- `specs/guides/` exists and is accurate
- `specs/template-spec/` exists (templates)
- Agent-specific adapters point to `AGENTS.md`

### `/prd {feature}` (planning only; no source changes)

Goal: produce a PRD in `specs/active/{slug}/prd.md` with measurable acceptance criteria and an implementation plan.

Hard rules:

- Do not modify production source code in PRD phase.
- Must identify similar patterns (3–5 files) and cite them in PRD.

### `/implement {slug}` (implementation)

Goal: implement exactly the PRD scope, following discovered patterns.

Hard rules:

- No scope creep: update PRD first if requirements change.
- Keep workspace updated (`tasks.md`, `recovery.md`).

### `/test {slug}` (tests)

Goal: expand tests to cover changed behavior and edge cases, matching project test style.

### `/review {slug}` (quality gates + knowledge capture + archive)

Goal:

- Run quality gates (tests/lint/types/docs)
- Extract reusable patterns to `specs/guides/`
- Archive the workspace to `specs/archive/`

### `/explore {topic}`

Goal: map codebase, entrypoints, and patterns without making changes.

### `/fix-issue {id}`

Goal: treat an issue like a PRD+implement flow; keep a workspace if non-trivial.

---

## 5) PRD Contract (Minimum Contents)

`specs/active/{slug}/prd.md` must include:

- Problem statement + user story
- Non-goals
- Scope boundaries and assumptions
- Acceptance criteria (testable)
- API/UX changes (if any)
- Data model changes (if any)
- Error handling + observability plan
- Test strategy (unit/integration, edge cases)
- Rollout/release notes (if needed)
- “Similar implementations” section listing 3–5 file paths

`specs/active/{slug}/tasks.md` must include:

- A sequenced checklist (small → big)
- Quality gates to run
- Where docs/patterns will be updated

`specs/active/{slug}/recovery.md` must include:

- Current status
- Commands to reproduce
- What files were changed
- Open questions/risks

---

## 6) Knowledge Capture Rules

After implementing a feature:

1. Add/refresh pattern docs under `specs/guides/` (prefer `specs/guides/patterns/` for reusable “how we do X here”).
2. Update guide cross-links so future agents can find the pattern quickly.
3. Keep docs “present tense”: document what exists **now**, not history.

---

## 7) Agent Adapters (Keep Small; Point Here)

These files are adapters for different runtimes and should **not** duplicate this document:

- **Claude Code**: `CLAUDE.md` and `.claude/`
- **Gemini CLI**: `.gemini/GEMINI.md` and `.gemini/`
- **OpenAI Codex CLI**: this repo root `AGENTS.md` is the primary entrypoint

If you add/update an adapter, ensure it:

- Points to `AGENTS.md` for workflow and standards
- Includes only tool/config specifics and common commands

---

## 8) Canonical Agent Roles (Same Mental Model Everywhere)

These “roles” are conceptual. A given runtime may implement them as subagents, commands, or just disciplined prompting.

### Planner / PRD

- Creates/updates the workspace under `specs/active/{slug}/`
- Produces `prd.md`, `tasks.md`, `recovery.md`, and a context pack in `README.md`
- Ensures **no production source** changes occur during PRD phase

### Expert / Implementer

- Implements exactly what’s in the PRD (no scope creep)
- Uses pattern-first approach (reads similar files first)
- Keeps workspace status current (`tasks.md`, `recovery.md`)

### Testing

- Adds tests that match project conventions
- Covers edge cases and failure modes implied by acceptance criteria
- Ensures quality gates will pass

### Docs & Vision (QA + Knowledge Capture)

- Runs quality gates (tests/lint/types/docs)
- Updates docs and guides so they match the current codebase
- Captures reusable patterns in `specs/guides/`
- Archives the completed workspace to `specs/archive/`

---

## 9) Bootstrap Procedure (Generic, Works for All Three Agents)

The bootstrap goal is: **every agent reads the same sources, produces the same artifacts, and validates with the same gates**.

### 9.1 Detect project reality (read-only)

Recommended scan commands (adjust per repo):

```bash
ls -la
rg -n "litestar|fastapi|django|flask" pyproject.toml 2>/dev/null || true
rg -n "\"react\"|\"vue\"|\"svelte\"|\"vite\"" package.json 2>/dev/null || true
find . -maxdepth 3 -type f \\( -name "pyproject.toml" -o -name "package.json" -o -name "Cargo.toml" -o -name "go.mod" \\)
```

Output: update **Project Profile** (Section 2) and ensure `specs/guides/README.md` reflects the truth.

### 9.2 Ensure shared directories exist (idempotent)

```bash
mkdir -p specs/active specs/archive
mkdir -p specs/guides/patterns specs/guides/workflows specs/guides/examples
mkdir -p specs/template-spec/research specs/template-spec/tmp
touch specs/active/.gitkeep specs/archive/.gitkeep
```

### 9.3 Create/align quality gates

- Authoritative: `specs/guides/quality-gates.yaml`
- Gates should include (as applicable): tests, lint, type-check, docs build, coverage policy.

### 9.4 Create/align agent adapters (minimal duplication)

Adapters should:

- Point to `AGENTS.md` for workflow/standards
- Configure tool permissions / model settings / command definitions
- Avoid duplicating architecture narratives (keep those in `specs/guides/` and `CLAUDE.md` if you want a repo-specific summary)

**Claude Code (recommended minimal contract)**

- `CLAUDE.md` should include:
  - Key repo commands (install/test/lint/docs)
  - High-level architecture pointers (file list, entrypoints)
  - A short “Read first: `AGENTS.md`” note
- `.claude/commands/` should provide `/prd`, `/implement`, `/test`, `/review`, `/explore`, `/fix-issue`, `/bootstrap` as wrappers around this workflow.

**Gemini CLI (recommended minimal contract)**

- `.gemini/GEMINI.md` should:
  - Point to `AGENTS.md`
  - Declare which folders to load as context (`specs/guides/`, active workspace)
  - Optionally include Gemini settings (`.gemini/settings.json`) and command prompts (`.gemini/commands/*.toml`)

**OpenAI Codex CLI (this environment)**

- `AGENTS.md` is the primary entrypoint.
- Optional: add a tiny `CODEX.md` or `.codex/` directory only if you need custom shortcuts; keep it adapter-only.

### 9.5 Alignment mode (when bootstrap already exists)

When re-running bootstrap, do **not** overwrite custom content blindly:

1. Inventory current adapters and guides.
2. Identify missing components.
3. Preserve custom sections verbatim.
4. Merge improvements into canonical sources (`AGENTS.md`, `specs/guides/`).

---

## 10) Verification (End Every Non-trivial Change Here)

Always attempt the repo’s preferred gates first (often via `make`):

```bash
make test
make lint
make docs
```

If the repo doesn’t provide `make` targets, use direct equivalents (e.g. `uv run pytest`, `uv run ruff check .`, etc.).

For PRD-only work, verification is:

- `git status --porcelain` shows no production changes (as defined by the repo; commonly `src/` or package dirs)

---

## 11) Embedded Workspace Templates (Copy Into `specs/template-spec/`)

These are minimal templates. Projects can extend them, but the filenames and intent should remain stable.

### `README.md` (Context Pack)

```md
# {feature slug}

## Context Pack

- Goal:
- Non-goals:
- Constraints:
- Similar implementations (3–5):
  - `path/to/file.py` — why it’s similar
- Decisions:
  - D1:
- Test plan:
  - Unit:
  - Integration:
- Verification commands:
  - `make test`
  - `make lint`
```

### `prd.md` (PRD skeleton)

```md
# PRD: {feature name}

## Summary

## Problem

## Goals / Non-goals

## Requirements

## Acceptance Criteria

## Technical Plan

## Testing Plan

## Rollout / Notes

## Similar Implementations
```

### `tasks.md` (checklist skeleton)

```md
# Tasks: {feature slug}

- [ ] Read `AGENTS.md` + relevant `specs/guides/*`
- [ ] Locate similar implementations (3–5)
- [ ] Implement smallest slice
- [ ] Add/extend tests
- [ ] Run quality gates
- [ ] Update docs/guides + capture patterns
- [ ] Archive workspace
```

### `recovery.md` (resume skeleton)

```md
# Recovery: {feature slug}

## Current status

## Commands to reproduce

## Files changed (planned/actual)

## Open questions / risks
```

---

## 12) Shared Context Rules (Keeping Agents “Knowledge-Equivalent”)

To keep Claude/Gemini/Codex aligned, every agent must:

1. Read `AGENTS.md` + `specs/guides/README.md` before acting.
2. Prefer updating `specs/guides/` over burying knowledge in chat logs.
3. Write decisions into the workspace context pack so another agent can continue without re-discovery.
4. Treat adapters as thin shims; the shared system is `AGENTS.md` + `specs/`.

---

## 13) Pattern Index (Quick Reference)

This section is a lightweight index to help any agent quickly find “how we do X here”.

Add new entries when a feature introduces a reusable approach. Put the **full** pattern write-up in `specs/guides/patterns/` and link it here.

### Current repository links

- Architecture: `specs/guides/plugin-architecture.md`
- Error handling: `specs/guides/error-handling.md`
- Testing: `specs/guides/testing-patterns.md`
- Pattern library index: `specs/guides/patterns/README.md`
- Runtime discovery (controllers): `specs/guides/patterns/runtime-discovery.md`

---

## 14) Optional: MCP Tool Detection + Strategy (Adapter-Owned)

If your agent runtime supports MCP tools, keep detection and strategy docs **inside the adapter folder**, not in `AGENTS.md`.

Recommended outputs:

- Claude: `.claude/mcp-strategy.md`
- Gemini: `.gemini/mcp-strategy.md`

Minimal strategy format:

- Reasoning: sequential thinking (fallback: manual checklist)
- Planning: planner tools (fallback: structured markdown plan)
- Research: Context7 (fallback: web search; fallback: repo grep)
- Analysis/Debug: analysis/debug tools (fallback: disciplined local reproduction + git bisect style reasoning)

Keep this doc short and practical: “When to use X” + “Fallback when X missing”.
