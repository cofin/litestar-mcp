Invoke the Expert agent to implement a feature with fully automated testing, documentation, knowledge capture, and archival.

**What this does:**
- Reads PRD and tasks from `specs/active/{slug}/`
- Researches Litestar/MCP patterns (Context7, WebSearch, codebase)
- Implements feature following AGENTS.md standards
- **Automatically invokes Testing agent** (creates comprehensive tests, must pass)
- **Automatically invokes Docs & Vision agent** (docs, quality gate, knowledge capture, archive)
- Returns only when entire workflow complete

**Usage:**
```
/implement websocket-mcp-transport
```

**Or for most recent spec:**
```
/implement
```

**The Expert will:**
1. Read PRD, tasks, recovery guide from specs/active/{slug}/
2. Read AGENTS.md for all litestar-mcp patterns (MANDATORY)
3. Research implementation approach:
   - Consult Context7 for latest Litestar documentation
   - WebSearch for MCP protocol specifics
   - Grep/Glob for similar patterns in codebase
4. Implement following strict standards:
   - Stringified type hints: `"Optional[str]"`, `"list[dict]"`
   - NO `from __future__ import annotations`
   - NO `|` union syntax (use `typing.Union`)
   - Litestar plugin patterns (InitPluginProtocol, CLIPlugin)
   - Error handling (inherit from Litestar exceptions)
5. Self-test during development: `uv run pytest tests/test_{feature}.py`
6. **AUTO-INVOKE Testing agent**:
   - Create unit tests (class-based, async support)
   - Create integration tests (real Litestar app)
   - Test edge cases (empty, errors, boundaries, async)
   - Achieve 85%+ coverage
   - ALL tests must pass before proceeding
7. **AUTO-INVOKE Docs & Vision agent**:
   - **Phase 1**: Update Sphinx documentation (docs/reference/, docs/usage/)
   - **Phase 2**: Quality gate validation (BLOCKS if criteria not met)
   - **Phase 3**: **Knowledge capture** - Extract patterns → update AGENTS.md
   - **Phase 4**: **Update guides** - Add patterns to specs/guides/ with examples
   - **Phase 5**: **Re-validate** - Tests, docs, consistency
   - **Phase 6**: Clean tmp/ and archive to specs/archive/
8. Return comprehensive completion summary

**This ONE command handles:**
✅ Implementation (Expert)
✅ Testing (automatic via Testing agent)
✅ Documentation (automatic via Docs & Vision)
✅ Knowledge capture (automatic - AGENTS.md + specs/guides/)
✅ Quality gate (automatic - must pass)
✅ Re-validation (automatic - ensures consistency)
✅ Archival (automatic - specs/archive/)

**After implementation:**
Feature is complete, tested, documented, patterns captured, and archived!
Ready for production use and future features benefit from captured patterns.
