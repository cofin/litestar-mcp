---
title: Contributing Guide
---

# Contributing Guide

Thank you for your interest in contributing to `litestar-mcp`. This guide covers local workspace setup, test and conformance suites, static analysis gates, and the documentation verification pipeline.

---

## Development Setup

### Prerequisites

- **Python**: `3.10` or newer (`3.10` through `3.14` supported)
- **`uv`**: Astral's Python package and environment manager
- **Node.js**: `24.18.1` (required only when running the official MCP protocol conformance suite via `make conformance` or `make conformance-queue`)

### Clone and Install

1. Clone the repository:

```bash
git clone https://github.com/cofin/litestar-mcp.git
cd litestar-mcp
```

2. Sync all runtime, optional extra, and development dependencies:

```bash
make install
```

`make install` pins Python `3.10` for the local `.venv` and runs `uv sync --all-extras --dev`.

---

## Running Tests & Protocol Conformance

### Unit, Integration, and Example Suites

```bash
# Run unit tests, integration tests, and runnable documentation examples
make test

# Run the test suite with parallel coverage reporting
make coverage

# Run a single test module or filter
uv run pytest tests/unit/test_plugin.py -q
```

### MCP 2026-07-28 Protocol Conformance

The repository validates protocol compliance against the pinned MCP conformance runner in `package-lock.json` under both in-process and distributed queue task executors:

```bash
# Run MCP 2026-07-28 conformance with the default in-process task executor
make conformance

# Run MCP 2026-07-28 conformance with QueueTaskExecutor (litestar-queues)
make conformance-queue
```

If `nodenv` is installed, the `Makefile` automatically selects Node.js `24.18.1` via `NODENV_VERSION`; otherwise it uses the active Node.js runtime on `PATH`.

---

## Code Quality & Static Analysis

Run the full linting and type-checking suite before opening a pull request:

```bash
# Run ruff check, ruff format, mypy, pyright, and slotscheck
make lint

# Run lint, tests, and coverage together
make check-all
```

Individual quality gates can also be invoked directly:

| Target | Purpose |
| :--- | :--- |
| `make ruff-check` | Lint `litestar_mcp`, `tests`, `docs/examples`, `tools/ci`, and `tools/docs` with Ruff. |
| `make ruff-format` | Format Python files (Markdown files are excluded to preserve snippet and directive blocks). |
| `make mypy` | Run strict Mypy type-checking across `litestar_mcp` and `tests`. |
| `make pyright` | Run Pyright type-checking across `litestar_mcp` and `tests`. |
| `make slotscheck` | Verify `__slots__` integrity across `litestar_mcp`. |
| `make validate-examples` | Verify `# start-example` / `# end-example` markers in `docs/examples`. |
| `make validate-pep723` | Validate inline PEP 723 dependency blocks in standalone runnable examples. |
| `make validate-uvx` | Execute `uvx` command snippets from the architecture and example guides. |

---

## Documentation Pipeline

The documentation site uses static AST extraction for API reference chapters, automated Click CLI introspection, and post-build artifact verification (`objects.inv`, `llms.txt`, `llms-full.txt`, `.nojekyll`, and legacy HTML redirect stubs).

```bash
# 1. Scan 100% of litestar_mcp/** for public docstring and PEP 257 field docstring compliance
make docs-scan

# 2. Regenerate tools/docs/generated/cli-reference.md from the Click command group
make docs-cli

# 3. Build the documentation site and run post-build artifact generation
make docs

# 4. Run the full strict CI gate (docs-scan + CLI check + clean build + postbuild + artifact verifier)
make docs-check

# 5. Serve the documentation locally with live reload
make docs-serve
```

---

## Coding Standards

1. **Type Annotations**: Use modern built-in generics (`list`, `dict`, `tuple`, `set`) and PEP 604 union syntax (`T | None`). Do not add `from __future__ import annotations` to modules where Litestar or Dishka inspects runtime annotations.
2. **Data Models**: Prefer `msgspec.Struct(..., frozen=True, gc=False)` for wire payloads and hot-path data structures, and `@dataclass(frozen=True)` where framework configuration dataclasses are expected.
3. **Docstrings**: Use Google-style docstrings on all public modules, classes, methods, and functions. Document dataclass and `msgspec.Struct` attributes using PEP 257 attribute docstrings (`"""Description."""` immediately below each field) rather than duplicating an `Attributes:` table in the class docstring.
4. **No Inline Comments**: Explain design intent in module, class, and function docstrings rather than inline code comments.
5. **Line Length**: Keep lines within `120` characters.

---

## Pull Request Checklist

1. Create a feature branch from `main`.
2. Add or update unit tests in `tests/` and runnable snippets in `docs/examples/` for new user-visible behavior.
3. Verify `make lint`, `make test`, and `make docs-check` pass with zero warnings or errors.
4. Open a pull request with a clear summary of the motivation, protocol impact, and verification steps.
