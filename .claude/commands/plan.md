Invoke the Planner agent to create a comprehensive requirement workspace for a new litestar-mcp feature.

**What this does:**
- Creates `specs/active/{feature-slug}/` directory structure
- Writes detailed PRD with MCP protocol and Litestar plugin considerations
- Creates actionable 7-phase task list
- Generates recovery guide for resuming work
- Identifies research questions for Expert

**Usage:**
```
/plan Add WebSocket transport for MCP protocol
```

**The Planner will:**
1. Analyze the requirement and research MCP/Litestar patterns
2. Create workspace: `specs/active/{slug}/`
3. Write comprehensive PRD covering:
   - MCP protocol compliance requirements
   - Litestar plugin integration points
   - Technical scope (REST API, CLI, schemas)
   - Testing strategy (pytest, 85% coverage)
   - Documentation requirements (Sphinx .rst)
4. Create 7-phase task breakdown:
   - Phase 1: Planning & Research ✅
   - Phase 2: Expert Research
   - Phase 3: Core Implementation (Expert)
   - Phase 4: Integration (Expert)
   - Phase 5: Testing (AUTO-INVOKED via Expert)
   - Phase 6: Documentation (AUTO-INVOKED via Expert)
   - Phase 7: Knowledge Capture & Archive (AUTO-INVOKED via Expert)
5. Write recovery guide with litestar-mcp specific patterns

**Output Structure:**
```
specs/active/{slug}/
├── prd.md          # Product Requirements Document
├── tasks.md        # 7-phase task checklist
├── recovery.md     # Recovery guide for any agent
├── README.md       # Workspace overview
├── research/       # Expert research findings (populated by Expert)
└── tmp/            # Temporary files (cleaned by Docs & Vision)
```

**After planning, run:**
- `/implement {slug}` to build the feature (fully automated: code → tests → docs → archive)
