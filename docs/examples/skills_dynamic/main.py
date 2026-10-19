"""Dynamic, request-aware MCP skills example.

Demonstrates composing a static ``FilesystemSkillSource`` with a dynamic
``WorkspaceSkillSource`` that constructs skills in memory with ``Skill.build``,
filters visibility by the incoming ``X-Workspace-Group`` request header, and
lazily serves a secondary ``runbook.md`` file via ``read_file``.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "litestar[standard]>=2.0",
#   "litestar-mcp",
#   "uvicorn",
# ]
# ///

from dataclasses import replace
from pathlib import Path
from typing import Any

from litestar import Litestar, Request

from litestar_mcp import (
    LitestarMCP,
    MCPConfig,
    MCPSkillsConfig,
    Skill,
    SkillFile,
)

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


class WorkspaceSkillSource:
    """In-memory skill source scoped by the ``X-Workspace-Group`` header."""

    def __init__(self) -> "None":
        self._runbook_bytes = (
            b"# Incident Runbook\n\n"
            b"1. Verify service health indicators.\n"
            b"2. Check deployment rollouts for the active workspace.\n"
        )
        lazy_runbook = replace(
            SkillFile.from_content("platform-runbook", "runbook.md", self._runbook_bytes),
            content=None,
        )
        self._skills: dict[str, tuple[str, Skill]] = {
            "platform-runbook": (
                "platform",
                Skill.build(
                    name="platform-runbook",
                    description="Operational runbook for the platform engineering group.",
                    body="Follow the attached runbook when triaging workspace incidents.",
                    files={"runbook.md": lazy_runbook},
                ),
            ),
            "analytics-playbook": (
                "analytics",
                Skill.build(
                    name="analytics-playbook",
                    description="Query optimization playbook for the analytics group.",
                    body="Inspect partition pruning before escalating slow warehouse jobs.",
                ),
            ),
        }

    async def list_skills(self, request: "Request[Any, Any, Any]") -> "list[Skill]":
        """Return only the skills matching the caller's workspace group."""
        group = self._caller_group(request)
        return [skill for required_group, skill in self._skills.values() if required_group == group]

    async def get_skill(
        self,
        request: "Request[Any, Any, Any]",
        name: "str",
    ) -> "Skill | None":
        """Resolve a single skill by name when permitted for the caller's group."""
        entry = self._skills.get(name)
        if entry is None:
            return None
        required_group, skill = entry
        if required_group != self._caller_group(request):
            return None
        return skill

    async def read_file(
        self,
        request: "Request[Any, Any, Any]",
        skill: "Skill",
        file: "SkillFile",
    ) -> "bytes":
        """Serve ``SKILL.md`` from memory and ``runbook.md`` on demand."""
        del request
        if file.content is not None:
            return file.content
        if skill.name == "platform-runbook" and file.relative_path == "runbook.md":
            return self._runbook_bytes
        raise FileNotFoundError(file.relative_path)

    def _caller_group(self, request: "Request[Any, Any, Any] | None") -> "str":
        if request is None:
            return "public"
        return request.headers.get("x-workspace-group", "public").strip().lower()


def build_app() -> "Litestar":
    """Construct a Litestar app combining filesystem and dynamic skill sources."""
    mcp_config = MCPConfig(
        name="Dynamic Skills API",
        cache_scope="private",
        skills=MCPSkillsConfig(
            paths=[SKILLS_ROOT],
            sources=[WorkspaceSkillSource()],
            directory_read=True,
        ),
    )
    return Litestar(
        route_handlers=[],
        plugins=[LitestarMCP(mcp_config)],
    )


app = build_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
