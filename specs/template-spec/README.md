# {Feature Name}

This workspace contains the planning, implementation, testing, and documentation for the **{Feature Name}** feature in litestar-mcp.

## Quick Links

- [PRD (Product Requirements Document)](prd.md) - Full requirements and MCP protocol details
- [Tasks Checklist](tasks.md) - 7-phase implementation checklist
- [Recovery Guide](recovery.md) - Resume guide for any agent
- [Research Findings](research/) - Expert's research and analysis

## Status

**Current Phase**: {Phase number and name}
**Status**: {Not Started / In Progress / Complete}
**Last Updated**: {Date}
**Assigned To**: {Agent name or "Ready for /implement"}

## Workflow

This spec follows the automated workflow:

1. **Plan** (`/plan`) - Planner agent created this workspace
2. **Implement** (`/implement`) - Expert agent:
   - Implements feature following AGENTS.md patterns
   - Auto-invokes Testing agent (creates comprehensive tests)
   - Auto-invokes Docs & Vision agent (docs, QA, knowledge capture, archive)
3. **Complete** - Everything done automatically!

## Files

- **prd.md** - Full requirements, MCP protocol compliance, acceptance criteria
- **tasks.md** - 7-phase checklist with handoff notes
- **recovery.md** - Resume guide with agent-specific instructions
- **research/** - Expert findings and analysis (populated during Phase 2)
- **tmp/** - Temporary files (cleaned by Docs & Vision in Phase 7)
- **progress.md** - Running log (created by agents during implementation)

## Automated Phases

When you run `/implement {requirement-slug}`, the Expert agent automatically handles:

✅ **Implementation** (Expert):
   - Research MCP protocol and Litestar patterns
   - Implement following strict type standards
   - Self-test during development

✅ **Testing** (Testing agent - AUTO):
   - Create unit tests (class-based, async support)
   - Create integration tests (real Litestar app)
   - Test edge cases (empty, errors, boundaries, async, CLI)
   - Achieve 85%+ coverage
   - All tests must pass

✅ **Documentation** (Docs & Vision - AUTO):
   - Update Sphinx documentation (API reference + usage guides)
   - Quality gate validation (BLOCKS if criteria not met)
   - **Knowledge capture**: Extract patterns → update AGENTS.md
   - **Update guides**: Add patterns to specs/guides/ with examples
   - **Re-validate**: Tests, docs, consistency
   - Clean tmp/ and archive to specs/archive/

No manual intervention required between phases!

## litestar-mcp Specific Context

**This is an MCP (Model Context Protocol) integration plugin for Litestar.**

Key considerations for this feature:
- MCP protocol compliance (follow spec at https://spec.modelcontextprotocol.io/)
- Litestar plugin architecture (InitPluginProtocol, CLIPlugin)
- REST API endpoints under `/mcp/`
- CLI integration via `litestar mcp` commands
- JSON Schema generation for tool parameters
- Type safety (stringified hints, no `|` syntax)
- Testing: pytest with asyncio, 85% coverage
- Documentation: Sphinx with .rst files

## Post-Implementation

After `/implement` completes:
- ✅ Feature is implemented and tested
- ✅ Documentation is complete
- ✅ New patterns are captured in AGENTS.md
- ✅ Guides are updated with examples
- ✅ Spec is archived to specs/archive/
- ✅ Knowledge is preserved for future features

Check the completion report in the archived spec for details!
