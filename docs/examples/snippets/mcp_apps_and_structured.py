"""Snippet: MCP Apps UI metadata, structured outputSchema, and ui:// resources."""

# --8<-- [start:example]
import msgspec
from litestar import Litestar, get

from litestar_mcp import LitestarMCP, mcp_tool


class ReportSummary(msgspec.Struct):
    report_id: str
    title: str
    total_items: int
    status: str


@mcp_tool(
    "report_summary",
    output_schema=True,
    title="Report Summary",
    description="Generate a structured report summary linked to the interactive UI resource.",
    meta={"ui": {"resourceUri": "ui://reports/view.html"}},
)
@get("/apps/summary", sync_to_thread=False)
def report_summary(report_id: str) -> ReportSummary:
    return ReportSummary(
        report_id=report_id,
        title=f"Report {report_id}",
        total_items=12,
        status="ready",
    )


@get(
    "/apps/view",
    mcp_resource="reports_view",
    mcp_resource_uri="ui://reports/view.html",
    mcp_resource_mime_type="text/html;profile=mcp-app",
    sync_to_thread=False,
)
def report_view() -> str:
    return '<main data-app="reports-view"><h1>Report Summary</h1></main>'


app = Litestar(route_handlers=[report_summary, report_view], plugins=[LitestarMCP()])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
