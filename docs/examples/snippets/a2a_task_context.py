"""A2A task context snippet demonstrating status, thoughts, and artifacts."""

from litestar import Litestar, post

from litestar_mcp import A2AConfig, A2APlugin, Artifact, TaskContext, TextPart


@post("/skills/analyze", opt={"a2a_skill": "analyze", "a2a_description": "Analyze query"})
async def analyze_task(query: str, ctx: TaskContext) -> dict[str, str]:
    """Analyze query and report progress."""
    await ctx.thought("Parsing query arguments...")
    await ctx.report_status("working", message="Analysis in progress")
    await ctx.emit_artifact(Artifact(name="report.txt", parts=[TextPart(text="Initial findings")]))
    return {"status": "complete"}


app = Litestar(
    route_handlers=[analyze_task],
    plugins=[A2APlugin(config=A2AConfig(name="AnalyzerAgent", base_path="/a2a"))],
)
