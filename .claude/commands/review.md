Invoke the Docs & Vision agent for documentation, quality gate, knowledge capture, and archival.

**What this does:**
- Validates all acceptance criteria met (BLOCKS if not)
- Updates Sphinx documentation (docs/reference/, docs/usage/)
- Creates/updates guides in specs/guides/
- **Captures new patterns in AGENTS.md** (CRITICAL)
- **Updates guides with learned patterns**
- **Re-validates after updates**
- Cleans workspace and archives requirement

**Usage:**
```
/review websocket-mcp-transport
```

**Or for most recent spec:**
```
/review
```

**The Docs & Vision agent will:**

### Phase 1: Quality Gate
1. Read PRD for acceptance criteria
2. Verify ALL criteria met:
   - Feature functional ✅
   - Tests passing ✅
   - Coverage ≥ 85% ✅
   - Code follows AGENTS.md standards ✅
3. **BLOCKS** if any criterion not met - requests fixes

### Phase 2: Documentation
1. Update Sphinx API reference (docs/reference/)
2. Update usage guides (docs/usage/)
3. Add working code examples
4. Build docs: `make docs`
5. Validate examples work

### Phase 3: Knowledge Capture (CRITICAL)
1. Analyze implementation for new patterns:
   - Error handling approaches
   - Testing patterns
   - MCP integration techniques
   - Litestar plugin patterns
   - Type annotation patterns
   - Async patterns
2. **Update AGENTS.md** with discovered patterns:
   - Add to relevant sections
   - Include working code examples
   - Document when/why to use pattern
3. **Update specs/guides/** with patterns:
   - Create or update relevant guide files
   - Cross-reference with AGENTS.md
   - Provide complete working examples

### Phase 4: Re-validation (CRITICAL)
1. Re-run tests: `uv run pytest tests/`
2. Rebuild docs: `make docs`
3. Verify pattern consistency:
   - New patterns match implementation ✅
   - Examples work ✅
   - No breaking changes ✅
   - Cross-references correct ✅
4. **BLOCKS** if re-validation fails

### Phase 5: Cleanup & Archive
1. Remove all tmp/ files
2. Move specs/active/{slug} to specs/archive/
3. Create completion record

### Phase 6: Completion Report
Generate comprehensive summary:
- Implementation summary
- Files modified
- Tests added
- Documentation updated
- **New patterns captured** (with locations)
- Quality metrics
- Archive location

**Note:** This command is typically **not needed** manually because `/implement` automatically invokes Docs & Vision.

Use this only if you need to:
- Re-run validation after manual changes
- Regenerate documentation
- Force re-archival
- **Manually capture patterns** after manual implementation

**After review:**
- Feature documented ✅
- Quality gate passed ✅
- **Patterns captured in AGENTS.md** ✅
- **Guides updated with new patterns** ✅
- **Re-validation passed** ✅
- Archived to specs/archive/ ✅

Future features benefit from the captured knowledge!
