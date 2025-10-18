# Tasks: {Feature Name}

## Phase 1: Planning & Research ✅
- [x] 1.1 Create requirement workspace
- [x] 1.2 Write comprehensive PRD
- [x] 1.3 Create 7-phase task breakdown
- [x] 1.4 Identify research questions for Expert

## Phase 2: Expert Research
- [ ] 2.1 Read AGENTS.md for litestar-mcp patterns (MANDATORY)
- [ ] 2.2 Research MCP protocol specification
- [ ] 2.3 Research Litestar plugin integration patterns
- [ ] 2.4 Review similar implementations in codebase
- [ ] 2.5 Document findings in research/

## Phase 3: Core Implementation (Expert)
- [ ] 3.1 Create/update core module (litestar_mcp/{feature}.py)
- [ ] 3.2 Implement business logic following AGENTS.md patterns
- [ ] 3.3 Update plugin class if needed (litestar_mcp/plugin.py)
- [ ] 3.4 Update routes if needed (litestar_mcp/routes.py)
- [ ] 3.5 Handle edge cases (empty inputs, errors, boundaries)
- [ ] 3.6 Self-test: `uv run pytest tests/test_{feature}.py`

## Phase 4: Integration (Expert)
- [ ] 4.1 Update MCP schema definitions (litestar_mcp/schema.py)
- [ ] 4.2 Update CLI commands if applicable (litestar_mcp/cli.py)
- [ ] 4.3 Update configuration (litestar_mcp/config.py)
- [ ] 4.4 Update schema builder if needed (litestar_mcp/schema_builder.py)
- [ ] 4.5 Integration testing with real Litestar app

## Phase 5: Testing (Testing Agent - AUTO-INVOKED)
- [ ] 5.1 Create unit tests (tests/test_{feature}.py)
   - [ ] Test basic functionality
   - [ ] Test async patterns
   - [ ] Test with mocks/fixtures
- [ ] 5.2 Create integration tests
   - [ ] Test with real Litestar app
   - [ ] Test MCP protocol compliance
   - [ ] Test REST endpoints
- [ ] 5.3 Test edge cases
   - [ ] Empty inputs
   - [ ] Error conditions
   - [ ] Boundary values
   - [ ] Async edge cases
   - [ ] CLI context limitations (if applicable)
- [ ] 5.4 Verify coverage: `uv run pytest --cov=litestar_mcp --cov-fail-under=85`
- [ ] 5.5 All tests passing

## Phase 6: Documentation (Docs & Vision - AUTO-INVOKED)
- [ ] 6.1 Update Sphinx API reference
   - [ ] Create/update docs/reference/{feature}.rst
   - [ ] Add module to docs/reference/index.rst
- [ ] 6.2 Update usage guides
   - [ ] Update docs/usage/examples.rst
   - [ ] Update docs/usage/index.rst if needed
- [ ] 6.3 Add working code examples
   - [ ] Verify examples work
   - [ ] Test examples in tmp/
- [ ] 6.4 Build docs: `make docs` (verify no errors)
- [ ] 6.5 Update README.md if needed

## Phase 7: Knowledge Capture & Archive (Docs & Vision - AUTO-INVOKED)
- [ ] 7.1 Extract new patterns from implementation
   - [ ] Identify error handling patterns
   - [ ] Identify testing patterns
   - [ ] Identify MCP integration patterns
   - [ ] Identify Litestar plugin patterns
- [ ] 7.2 Update AGENTS.md with new patterns
   - [ ] Add patterns to relevant sections
   - [ ] Include working code examples
   - [ ] Document when/why to use patterns
- [ ] 7.3 Update specs/guides/ with detailed guides
   - [ ] Create or update relevant guide files
   - [ ] Cross-reference with AGENTS.md
   - [ ] Provide complete examples
- [ ] 7.4 Re-validate
   - [ ] Re-run tests: `uv run pytest tests/`
   - [ ] Rebuild docs: `make docs`
   - [ ] Verify consistency
- [ ] 7.5 Clean and archive
   - [ ] Clean tmp/ directory
   - [ ] Move to specs/archive/
   - [ ] Create completion record

## Handoff Notes

**To Expert Agent**:
- Read PRD thoroughly for MCP protocol requirements
- Read AGENTS.md for all litestar-mcp patterns (MANDATORY)
- Start with Phase 2 (Research) before implementing
- Document findings in research/ before coding
- Update tasks.md and recovery.md as you progress
- Will auto-invoke Testing and Docs & Vision agents when ready

**To Testing Agent** (AUTO-INVOKED):
- Read PRD for acceptance criteria
- Read recovery.md for implementation details
- Follow pytest patterns from AGENTS.md
- Achieve 85%+ coverage
- All tests must pass before returning control

**To Docs & Vision** (AUTO-INVOKED):
- Quality gate: Verify all PRD criteria met
- Update Sphinx documentation
- Extract and capture new patterns
- Update AGENTS.md and specs/guides/
- Re-validate after updates
- Clean and archive when complete
