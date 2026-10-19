"""Wire-level acceptance tests for dynamic and filesystem SkillSource composition."""

import json
import logging
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp import (
    MCP,
    LitestarMCP,
    MCPConfig,
    MCPSkillsConfig,
    MCPStdioContext,
    Skill,
    SkillFile,
)
from litestar_mcp.mcp.skills import compute_digest
from litestar_mcp.mcp.stdio import run_stdio_async
from tests.conftest import BridgeBytesSink, BridgeQueuedBytesSource
from tests.unit.conftest import mcp_post

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from litestar import Request


def _write_disk_skill(
    root: "Path", name: "str", description: "str", extra: "Mapping[str, bytes] | None" = None
) -> "None":
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    for rel_path, data in (extra or {}).items():
        target = skill_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class _TenantSkillSource:
    """Request-aware dynamic source filtering skills by X-User header."""

    def __init__(
        self,
        skills: "Sequence[Skill]" = (),
        *,
        visibility: "Mapping[str, set[str]] | None" = None,
        lazy_files: "Mapping[tuple[str, str], bytes] | None" = None,
        list_exc: "Exception | None" = None,
        get_exc: "Exception | None" = None,
        read_exc: "Exception | None" = None,
    ) -> "None":
        self.skills: list[Skill] = list(skills)
        self.visibility: dict[str, set[str]] = {k: set(v) for k, v in (visibility or {}).items()}
        self.lazy_files: dict[tuple[str, str], bytes] = dict(lazy_files or {})
        self.list_exc = list_exc
        self.get_exc = get_exc
        self.read_exc = read_exc

    def _can_view(self, request: "Request[Any, Any, Any]", skill_name: "str") -> "bool":
        if skill_name not in self.visibility:
            return True
        caller = request.headers.get("x-user", "")
        return caller in self.visibility[skill_name]

    async def list_skills(self, request: "Request[Any, Any, Any]", /) -> "Sequence[Skill]":
        if self.list_exc is not None:
            raise self.list_exc
        return [s for s in self.skills if self._can_view(request, s.name)]

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
        if self.get_exc is not None:
            raise self.get_exc
        if not self._can_view(request, name):
            return None
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    async def read_file(self, request: "Request[Any, Any, Any]", skill: "Skill", file: "SkillFile", /) -> "bytes":
        del request
        if self.read_exc is not None:
            raise self.read_exc
        key = (skill.name, file.relative_path)
        if key not in self.lazy_files:
            msg = f"Missing file {key}"
            raise LookupError(msg)
        return self.lazy_files[key]


def test_skills_list_is_filtered_per_caller_and_sorted_across_sources(tmp_path: "Path") -> "None":
    """skills/list combines filesystem and dynamic sources, filters per caller, and sorts by name."""
    _write_disk_skill(tmp_path, "common-runbook", "Disk runbook")
    source_one = _TenantSkillSource(
        [
            Skill.build("zeta-ops", "Zeta ops", files={"steps.md": "# Steps\n"}),
            Skill.build("alice-playbook", "Alice playbook"),
        ],
        visibility={"alice-playbook": {"alice"}, "zeta-ops": {"alice", "bob"}},
    )
    source_two = _TenantSkillSource(
        [Skill.build("bob-guide", "Bob guide")],
        visibility={"bob-guide": {"bob"}},
    )
    config = MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path], sources=[source_one, source_two]))
    app = Litestar(plugins=[LitestarMCP(config)], logging_config=None)

    with TestClient(app=app) as client:
        alice_skills = mcp_post(client, "skills/list", headers={"X-User": "alice"}).json()["result"]["skills"]
        bob_skills = mcp_post(client, "skills/list", headers={"X-User": "bob"}).json()["result"]["skills"]
        anon_skills = mcp_post(client, "skills/list").json()["result"]["skills"]

    assert [s["frontmatter"]["name"] for s in alice_skills] == [
        "alice-playbook",
        "common-runbook",
        "zeta-ops",
    ]
    assert [s["frontmatter"]["name"] for s in bob_skills] == [
        "bob-guide",
        "common-runbook",
        "zeta-ops",
    ]
    assert [s["frontmatter"]["name"] for s in anon_skills] == ["common-runbook"]


def test_skills_list_paginates_across_sources(tmp_path: "Path") -> "None":
    """skills/list paginates deterministically across mixed filesystem and dynamic sources."""
    _write_disk_skill(tmp_path, "bravo", "Bravo disk")
    dyn_a = _TenantSkillSource([Skill.build("delta", "Delta"), Skill.build("alpha", "Alpha")])
    dyn_b = _TenantSkillSource([Skill.build("charlie", "Charlie")])
    config = MCPConfig(
        list_page_size=2,
        skills=MCPSkillsConfig(paths=[tmp_path], sources=[dyn_a, dyn_b]),
    )
    app = Litestar(plugins=[LitestarMCP(config)], logging_config=None)

    with TestClient(app=app) as client:
        first_page = mcp_post(client, "skills/list").json()["result"]
        assert [s["frontmatter"]["name"] for s in first_page["skills"]] == ["alpha", "bravo"]
        assert "nextCursor" in first_page

        second_page = mcp_post(client, "skills/list", {"cursor": first_page["nextCursor"]}).json()["result"]
        assert [s["frontmatter"]["name"] for s in second_page["skills"]] == ["charlie", "delta"]
        assert "nextCursor" not in second_page


def test_hidden_skill_is_unknown_to_every_skill_method() -> "None":
    """A hidden skill returns the exact same -32602 payload as an unknown skill on every method."""
    secret = Skill.build("secret", "Secret skill", files={"sub/doc.md": "# Secret Doc\n"})
    source = _TenantSkillSource([secret], visibility={"secret": {"alice"}})
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])

    with TestClient(app=app) as client:
        alice_get = mcp_post(
            client,
            "skills/get",
            {"uri": "skill://secret/SKILL.md"},
            headers={"X-User": "alice"},
        ).json()
        assert "result" in alice_get

        for method, hidden_uri, missing_uri in (
            ("skills/get", "skill://secret/SKILL.md", "skill://unknown/SKILL.md"),
            ("resources/read", "skill://secret/SKILL.md", "skill://unknown/SKILL.md"),
            ("resources/read", "skill://secret/sub/doc.md", "skill://unknown/sub/doc.md"),
            ("resources/directory/read", "skill://secret", "skill://unknown"),
            ("resources/directory/read", "skill://secret/sub", "skill://unknown/sub"),
        ):
            hidden_err = mcp_post(
                client,
                method,
                {"uri": hidden_uri},
                headers={"X-User": "bob"},
            ).json()["error"]
            missing_err = mcp_post(
                client,
                method,
                {"uri": missing_uri},
                headers={"X-User": "bob"},
            ).json()["error"]
            assert hidden_err["code"] == missing_err["code"] == -32602
            assert hidden_err["message"] == missing_err["message"]
            assert hidden_err["data"] == {"uri": hidden_uri}


def test_resources_list_includes_only_visible_skill_files() -> "None":
    """resources/list includes only files belonging to skills visible to the caller."""
    s_alice = Skill.build("alice-only", "Alice skill", files={"ref.txt": "alice ref"})
    s_public = Skill.build("public-skill", "Public skill")
    source = _TenantSkillSource([s_alice, s_public], visibility={"alice-only": {"alice"}})
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])

    with TestClient(app=app) as client:
        alice_uris = [
            r["uri"]
            for r in mcp_post(client, "resources/list", headers={"X-User": "alice"}).json()["result"]["resources"]
            if r["uri"].startswith("skill://")
        ]
        bob_uris = [
            r["uri"]
            for r in mcp_post(client, "resources/list", headers={"X-User": "bob"}).json()["result"]["resources"]
            if r["uri"].startswith("skill://")
        ]

    assert alice_uris == [
        "skill://alice-only/SKILL.md",
        "skill://alice-only/ref.txt",
        "skill://public-skill/SKILL.md",
    ]
    assert bob_uris == ["skill://public-skill/SKILL.md"]


def test_source_changes_are_served_without_restart() -> "None":
    """Mutating a dynamic source's backing store immediately reflects in subsequent requests."""
    source = _TenantSkillSource([Skill.build("live", "Version 1", files={"note.txt": "v1"})])
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])

    with TestClient(app=app) as client:
        first = mcp_post(client, "skills/get", {"uri": "skill://live/SKILL.md"}).json()["result"]["skill"]
        assert first["frontmatter"]["description"] == "Version 1"

        source.skills[:] = [
            Skill.build("live", "Version 2", files={"note.txt": "v2-updated"}),
            Skill.build("newly-added", "Added live"),
        ]

        listed = mcp_post(client, "skills/list").json()["result"]["skills"]
        assert [s["frontmatter"]["name"] for s in listed] == ["live", "newly-added"]

        second = mcp_post(client, "skills/get", {"uri": "skill://live/SKILL.md"}).json()["result"]["skill"]
        assert second["frontmatter"]["description"] == "Version 2"

        read_res = mcp_post(client, "resources/read", {"uri": "skill://live/note.txt"}).json()["result"]
        assert read_res["contents"][0]["text"] == "v2-updated"


def test_resources_read_serves_dynamic_content_matching_its_manifest() -> "None":
    """resources/read serves both inline and lazy dynamic skill files when bytes match the manifest."""
    lazy_payload = b"# Lazy Reference\n"
    lazy_file = SkillFile(
        relative_path="lazy.md",
        path=None,
        uri="skill://hybrid/lazy.md",
        mime_type="text/markdown",
        size=len(lazy_payload),
        digest=compute_digest(lazy_payload),
        content=None,
    )
    skill = Skill.build(
        "hybrid",
        "Hybrid inline and lazy skill",
        body="# Hybrid\n",
        files={"inline.txt": "inline payload", "lazy.md": lazy_file},
    )
    source = _TenantSkillSource([skill], lazy_files={("hybrid", "lazy.md"): lazy_payload})
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])

    with TestClient(app=app) as client:
        inline_res = mcp_post(client, "resources/read", {"uri": "skill://hybrid/inline.txt"}).json()["result"]
        lazy_res = mcp_post(client, "resources/read", {"uri": "skill://hybrid/lazy.md"}).json()["result"]

    assert inline_res["contents"][0]["text"] == "inline payload"
    assert lazy_res["contents"][0]["text"] == "# Lazy Reference\n"


def test_resources_read_rejects_dynamic_bytes_that_drift_from_the_manifest() -> "None":
    """resources/read returns -32603 SkillIntegrityError when lazy bytes do not match manifest digest."""
    expected = b"manifest bytes"
    lazy_file = SkillFile(
        relative_path="drift.txt",
        path=None,
        uri="skill://drift/drift.txt",
        mime_type="text/plain",
        size=len(expected),
        digest=compute_digest(expected),
        content=None,
    )
    skill = Skill.from_content(
        "---\nname: drift\ndescription: Drift skill\n---\n",
        files={"drift.txt": lazy_file},
    )
    source = _TenantSkillSource([skill], lazy_files={("drift", "drift.txt"): b"tampered bytes"})
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])

    with TestClient(app=app) as client:
        resp = mcp_post(client, "resources/read", {"uri": "skill://drift/drift.txt"}).json()

    assert resp["error"]["code"] == -32603
    assert resp["error"]["data"]["error"] == "SkillIntegrityError"


def test_first_source_wins_a_name_collision_and_logs_it(
    tmp_path: "Path",
    caplog: "pytest.LogCaptureFixture",
) -> "None":
    """When filesystem and dynamic sources share a skill name, filesystem wins and logs a warning."""
    _write_disk_skill(tmp_path, "overlap", "From filesystem")
    dyn_source = _TenantSkillSource([Skill.build("overlap", "From dynamic")])
    config = MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path], sources=[dyn_source]))
    app = Litestar(plugins=[LitestarMCP(config)], logging_config=None)

    with TestClient(app=app) as client, caplog.at_level(logging.WARNING, logger="litestar_mcp.mcp.skills"):
        listed = mcp_post(client, "skills/list").json()["result"]["skills"]

    assert len(listed) == 1
    assert listed[0]["frontmatter"]["description"] == "From filesystem"
    assert any("overlap" in record.message for record in caplog.records)


def test_source_failures_are_internal_errors_without_detail() -> "None":
    """Unexpected source exceptions return -32603 without leaking exception messages."""
    secret_detail = "postgres://admin:supersecret@db.internal:5432/prod"
    failing_source = _TenantSkillSource(
        list_exc=RuntimeError(secret_detail),
        get_exc=RuntimeError(secret_detail),
    )
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[failing_source])))])

    with TestClient(app=app) as client:
        for method, params in (
            ("skills/list", {}),
            ("skills/get", {"uri": "skill://any/SKILL.md"}),
            ("resources/list", {}),
            ("resources/read", {"uri": "skill://any/SKILL.md"}),
            ("resources/directory/read", {"uri": "skill://any"}),
        ):
            body = mcp_post(client, method, params).json()
            assert body["error"]["code"] == -32603
            assert secret_detail not in json.dumps(body)


def test_unreadable_lazy_file_is_internal_error_without_detail() -> "None":
    """OSError from source.read_file returns -32603 with only {'uri'} in error.data."""
    payload = b"content"
    lazy_file = SkillFile(
        relative_path="broken.txt",
        path=None,
        uri="skill://ioerr/broken.txt",
        mime_type="text/plain",
        size=len(payload),
        digest=compute_digest(payload),
        content=None,
    )
    skill = Skill.from_content(
        "---\nname: ioerr\ndescription: IO error skill\n---\n",
        files={"broken.txt": lazy_file},
    )
    secret_path = "/var/private/tenant-keys/secret.pem"
    source = _TenantSkillSource([skill], read_exc=OSError(f"Permission denied: {secret_path}"))
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])

    with TestClient(app=app) as client:
        body = mcp_post(client, "resources/read", {"uri": "skill://ioerr/broken.txt"}).json()

    assert body["error"]["code"] == -32603
    assert body["error"]["data"] == {"uri": "skill://ioerr/broken.txt"}
    assert secret_path not in json.dumps(body)


def test_dynamic_only_configuration_with_directory_read_disabled() -> "None":
    """MCPSkillsConfig(sources=[...], directory_read=False) disables resources/directory/read."""
    source = _TenantSkillSource([Skill.build("alpha", "Alpha")])
    config = MCPConfig(skills=MCPSkillsConfig(sources=[source], directory_read=False))
    app = Litestar(plugins=[LitestarMCP(config)], logging_config=None)

    with TestClient(app=app) as client:
        discover = mcp_post(client, "server/discover").json()["result"]
        assert discover["capabilities"]["extensions"]["io.modelcontextprotocol/skills"] == {"directoryRead": False}

        dir_resp = mcp_post(client, "resources/directory/read", {"uri": "skill://alpha"}).json()
        assert dir_resp["error"]["code"] == -32601


@pytest.mark.anyio
async def test_stdio_caller_identity_reaches_sources() -> "None":
    """Synthetic headers in MCPStdioContext reach dynamic SkillSource implementations over stdio."""
    source = _TenantSkillSource(
        [
            Skill.build("alice-stdio", "Alice stdio skill"),
            Skill.build("bob-stdio", "Bob stdio skill"),
        ],
        visibility={"alice-stdio": {"alice"}, "bob-stdio": {"bob"}},
    )
    mcp = MCP(name="stdio-dynamic-skills", config=MCPConfig(skills=MCPSkillsConfig(sources=[source])))
    request_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "skills/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "stdio-test", "version": "1.0"},
            }
        },
    }
    stdin = BridgeQueuedBytesSource(json.dumps(request_msg).encode("utf-8") + b"\n")
    stdout = BridgeBytesSink()
    with anyio.fail_after(10):
        exit_code = await run_stdio_async(
            mcp.app,
            stdio_context=MCPStdioContext(user="alice"),
            headers={"x-user": "alice"},
            stdin=stdin,
            stdout=stdout,
        )
    assert exit_code == 0
    lines = [line for line in stdout.buffer.decode("utf-8").splitlines() if line.strip()]
    response = json.loads(lines[0])
    skills = response["result"]["skills"]
    assert [s["frontmatter"]["name"] for s in skills] == ["alice-stdio"]
