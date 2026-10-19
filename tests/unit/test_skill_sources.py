"""Unit tests for SkillSource protocol, in-memory Skill/SkillFile models, and async SkillCatalog."""

import contextlib
import logging
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from litestar import Litestar, Request
from litestar.exceptions import ImproperlyConfiguredException
from litestar.testing import TestClient

from litestar_mcp import (
    FilesystemSkillSource,
    LitestarMCP,
    MCPConfig,
    MCPSkillsConfig,
    Skill,
    SkillFile,
    SkillIntegrityError,
    SkillSource,
    get_mcp_request_context,
)
from litestar_mcp.mcp.skills import SkillCatalog, compute_digest
from tests.unit.conftest import mcp_post

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _request_with_user(user: "str" = "alice") -> "Request[Any, Any, Any]":
    """Create a minimal Request mock carrying an X-User header."""
    req = MagicMock(spec=Request)
    req.headers = {"x-user": user}
    return req


class _InMemorySource:
    """Configurable request-aware SkillSource for unit testing."""

    def __init__(
        self,
        skills: "Sequence[Skill]" = (),
        *,
        acl: "Mapping[str, set[str]] | None" = None,
        lazy_bytes: "Mapping[tuple[str, str], bytes] | None" = None,
        read_error: "Exception | None" = None,
    ) -> "None":
        self._skills = tuple(skills)
        self._acl = dict(acl or {})
        self._lazy_bytes = dict(lazy_bytes or {})
        self._read_error = read_error
        self.read_calls: list[tuple[str, str]] = []
        self.observed_context_requests: list[Any] = []

    def _visible(self, request: "Request[Any, Any, Any]", skill: "Skill") -> "bool":
        if not self._acl:
            return True
        allowed_users = self._acl.get(skill.name)
        if allowed_users is None:
            return True
        caller = request.headers.get("x-user", "")
        return caller in allowed_users

    def _record_context_request(self) -> "None":
        with contextlib.suppress(RuntimeError):
            self.observed_context_requests.append(get_mcp_request_context().request)

    async def list_skills(self, request: "Request[Any, Any, Any]", /) -> "Sequence[Skill]":
        self._record_context_request()
        return [s for s in self._skills if self._visible(request, s)]

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
        self._record_context_request()
        for skill in self._skills:
            if skill.name == name and self._visible(request, skill):
                return skill
        return None

    async def read_file(self, request: "Request[Any, Any, Any]", skill: "Skill", file: "SkillFile", /) -> "bytes":
        self._record_context_request()
        del request
        self.read_calls.append((skill.name, file.relative_path))
        if self._read_error is not None:
            raise self._read_error
        key = (skill.name, file.relative_path)
        if key not in self._lazy_bytes:
            msg = f"Missing lazy bytes for {key}"
            raise LookupError(msg)
        return self._lazy_bytes[key]


def test_sources_satisfy_skill_source_protocol(tmp_path: "Path") -> "None":
    """Both FilesystemSkillSource and custom sources satisfy the runtime_checkable SkillSource protocol."""
    fs_source = FilesystemSkillSource([tmp_path])
    mem_source = _InMemorySource()
    assert isinstance(fs_source, SkillSource)
    assert isinstance(mem_source, SkillSource)


def test_sources_run_inside_the_mcp_request_context() -> "None":
    """Every source call executed through MCPHandlerService has get_mcp_request_context() bound."""
    skill = Skill.build("alpha", "Alpha skill", body="# Alpha\n")
    source = _InMemorySource([skill])
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])
    client = TestClient(app=app)

    resp = mcp_post(client, "skills/list")
    assert resp.status_code == 200
    assert len(source.observed_context_requests) == 1
    assert source.observed_context_requests[0] is not None


def test_skill_file_from_content_computes_manifest() -> "None":
    """SkillFile.from_content computes URI, MIME type, byte size, sha256 digest, and inline content."""
    sf = SkillFile.from_content("triage", "guides/overview.md", "# Overview\n")
    assert sf.path is None
    assert sf.relative_path == "guides/overview.md"
    assert sf.uri == "skill://triage/guides/overview.md"
    assert sf.mime_type == "text/markdown"
    assert sf.content == b"# Overview\n"
    assert sf.size == len(b"# Overview\n")
    assert sf.digest == compute_digest(b"# Overview\n")
    assert "content=" not in repr(sf)


def test_skill_build_renders_yaml_safe_frontmatter() -> "None":
    """Skill.build safely serializes special YAML characters in description and metadata."""
    description = "Triage: handle [urgent] & {quoted: true} items #1"
    skill = Skill.build(
        "triage",
        description,
        body="# Triage Guide\n",
        metadata={"owner": "team:core", "tags": ["a:b", "c"]},
        files={"reference.json": '{"ok": true}'},
    )
    assert skill.name == "triage"
    assert skill.description == description
    assert skill.uri == "skill://triage/SKILL.md"
    assert skill.frontmatter["name"] == "triage"
    assert skill.frontmatter["description"] == description
    assert skill.frontmatter["owner"] == "team:core"
    assert skill.frontmatter["tags"] == ["a:b", "c"]
    skill_md = skill.get_file("SKILL.md")
    assert skill_md is not None
    assert skill_md.content is not None
    assert b"# Triage Guide\n" in skill_md.content


def test_skill_build_rejects_reserved_metadata_keys() -> "None":
    """Skill.build rejects metadata dictionaries containing 'name' or 'description'."""
    with pytest.raises(ValueError, match="name"):
        Skill.build("triage", "Desc", metadata={"name": "other"})
    with pytest.raises(ValueError, match="description"):
        Skill.build("triage", "Desc", metadata={"description": "other"})


def test_skill_from_content_applies_filesystem_frontmatter_rules() -> "None":
    """Skill.from_content parses SKILL.md frontmatter and raises ValueError on invalid frontmatter."""
    skill = Skill.from_content(
        "---\nname: runbook\ndescription: Runbook skill\ncustom: 42\n---\n\n# Steps\n",
        files={"notes.txt": b"note"},
    )
    assert skill.name == "runbook"
    assert skill.description == "Runbook skill"
    assert skill.frontmatter["custom"] == 42
    assert [f.relative_path for f in skill.files] == ["SKILL.md", "notes.txt"]

    with pytest.raises(ValueError, match="frontmatter"):
        Skill.from_content("no frontmatter fence")
    with pytest.raises(ValueError, match="description"):
        Skill.from_content("---\nname: runbook\n---\n")


def test_skill_from_content_accepts_lazy_skill_files() -> "None":
    """Skill.from_content accepts pre-built lazy SkillFile instances with content=None."""
    payload = b"lazy payload"
    lazy_file = SkillFile(
        relative_path="docs/manual.txt",
        path=None,
        uri="skill://runbook/docs/manual.txt",
        mime_type="text/plain",
        size=len(payload),
        digest=compute_digest(payload),
        content=None,
    )
    skill = Skill.from_content(
        "---\nname: runbook\ndescription: Runbook skill\n---\n\n# Body\n",
        files={"docs/manual.txt": lazy_file},
    )
    found = skill.get_file("docs/manual.txt")
    assert found == lazy_file
    assert found is not None
    assert found.content is None


@pytest.mark.parametrize(
    "bad_name",
    ["", ".", "..", "a/b", "a\\b"],
)
def test_skill_rejects_unsafe_names(bad_name: "str") -> "None":
    """Skill rejects empty or path-traversing names."""
    with pytest.raises(ValueError):
        Skill.build(bad_name, "Description")


@pytest.mark.parametrize(
    "bad_rel_path",
    ["", "/abs.txt", "../escape.txt", "a/../b.txt", "./a.txt", "a/./b.txt", "a//b.txt", "a\\b.txt", "dir/"],
)
def test_skill_file_rejects_unsafe_paths(bad_rel_path: "str") -> "None":
    """SkillFile rejects relative paths that are empty, absolute, or contain dot/empty segments or backslashes."""
    with pytest.raises(ValueError):
        SkillFile.from_content("demo", bad_rel_path, b"data")


def test_skill_file_rejects_malformed_manifest() -> "None":
    """SkillFile enforces non-negative size, valid sha256 digest format, and inline content matching size/digest."""
    valid_digest = compute_digest(b"abc")
    with pytest.raises(ValueError, match="size"):
        SkillFile(
            relative_path="a.txt",
            path=None,
            uri="skill://demo/a.txt",
            mime_type="text/plain",
            size=-1,
            digest=valid_digest,
        )
    with pytest.raises(ValueError, match="digest"):
        SkillFile(
            relative_path="a.txt",
            path=None,
            uri="skill://demo/a.txt",
            mime_type="text/plain",
            size=3,
            digest="md5:1234",
        )
    with pytest.raises(ValueError, match="size"):
        SkillFile(
            relative_path="a.txt",
            path=None,
            uri="skill://demo/a.txt",
            mime_type="text/plain",
            size=99,
            digest=valid_digest,
            content=b"abc",
        )
    with pytest.raises(ValueError, match="digest"):
        SkillFile(
            relative_path="a.txt",
            path=None,
            uri="skill://demo/a.txt",
            mime_type="text/plain",
            size=3,
            digest=compute_digest(b"xyz"),
            content=b"abc",
        )


def test_skill_rejects_inconsistent_identity() -> "None":
    """Skill validates URI, frontmatter name/description, SKILL.md presence, foreign file URIs, and duplicates."""
    skill_md = SkillFile.from_content("demo", "SKILL.md", "---\nname: demo\ndescription: Desc\n---\n")
    foreign = SkillFile.from_content("other", "notes.txt", "hi")

    with pytest.raises(ValueError, match="uri"):
        Skill(
            name="demo",
            description="Desc",
            uri="skill://other/SKILL.md",
            frontmatter={"name": "demo", "description": "Desc"},
            files=(skill_md,),
        )
    with pytest.raises(ValueError, match=r"SKILL\.md"):
        Skill(
            name="demo",
            description="Desc",
            uri="skill://demo/SKILL.md",
            frontmatter={"name": "demo", "description": "Desc"},
            files=(SkillFile.from_content("demo", "notes.txt", "hi"),),
        )
    with pytest.raises(ValueError, match="uri"):
        Skill(
            name="demo",
            description="Desc",
            uri="skill://demo/SKILL.md",
            frontmatter={"name": "demo", "description": "Desc"},
            files=(skill_md, foreign),
        )
    dup = SkillFile.from_content("demo", "notes.txt", "hi")
    with pytest.raises(ValueError, match="duplicate"):
        Skill(
            name="demo",
            description="Desc",
            uri="skill://demo/SKILL.md",
            frontmatter={"name": "demo", "description": "Desc"},
            files=(skill_md, dup, dup),
        )


def test_skill_sorts_files_and_lists_directories() -> "None":
    """Skill sorts files by relative_path and lists root and nested directories."""
    skill = Skill.build(
        "demo",
        "Demo skill",
        files={"z.txt": "z", "a/b.txt": "b", "a/a.txt": "a"},
    )
    assert [f.relative_path for f in skill.files] == ["SKILL.md", "a/a.txt", "a/b.txt", "z.txt"]
    root_listing = skill.list_directory("")
    assert root_listing is not None
    assert [entry["name"] for entry in root_listing] == ["SKILL.md", "a", "z.txt"]
    nested_listing = skill.list_directory("a")
    assert nested_listing is not None
    assert [entry["name"] for entry in nested_listing] == ["a/a.txt", "a/b.txt"]
    assert skill.list_directory("missing") is None
    assert skill.list_directory("z.txt") is None


@pytest.mark.anyio
async def test_catalog_from_config_orders_paths_before_sources(tmp_path: "Path") -> "None":
    """SkillCatalog.from_config places FilesystemSkillSource(paths) ahead of config.sources."""
    skill_dir = tmp_path / "shared"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: shared\ndescription: From disk\n---\n", encoding="utf-8")

    dyn_skill = Skill.build("shared", "From dynamic")
    dyn_source = _InMemorySource([dyn_skill])
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path], sources=[dyn_source]))

    assert isinstance(catalog.sources[0], FilesystemSkillSource)
    assert catalog.sources[1] is dyn_source
    resolved = await catalog.get_skill(_request_with_user(), "shared")
    assert resolved is not None
    assert resolved.description == "From disk"


@pytest.mark.anyio
async def test_catalog_lists_skills_sorted_by_name_across_sources() -> "None":
    """SkillCatalog.list_skills merges skills across sources and sorts them by name."""
    source_a = _InMemorySource([Skill.build("zeta", "Zeta"), Skill.build("beta", "Beta")])
    source_b = _InMemorySource([Skill.build("alpha", "Alpha")])
    catalog = SkillCatalog([source_a, source_b])

    skills = await catalog.list_skills(_request_with_user())
    assert [s.name for s in skills] == ["alpha", "beta", "zeta"]


@pytest.mark.anyio
async def test_catalog_first_source_wins_and_logs_shadowed_skill(
    caplog: "pytest.LogCaptureFixture",
) -> "None":
    """When two sources return the same skill name, the earlier source wins and a WARNING is logged."""
    s1 = _InMemorySource([Skill.build("shared", "First source")])
    s2 = _InMemorySource([Skill.build("shared", "Second source")])
    catalog = SkillCatalog([s1, s2])

    with caplog.at_level(logging.WARNING, logger="litestar_mcp.mcp.skills"):
        skills = await catalog.list_skills(_request_with_user())

    assert len(skills) == 1
    assert skills[0].description == "First source"
    assert any("shared" in record.message for record in caplog.records)


@pytest.mark.anyio
async def test_catalog_falls_through_sources_that_hide_a_name() -> "None":
    """If an earlier source returns None for a name, get_skill falls through to later sources."""
    s1 = _InMemorySource([Skill.build("shared", "Restricted")], acl={"shared": {"alice"}})
    s2 = _InMemorySource([Skill.build("shared", "Public fallback")])
    catalog = SkillCatalog([s1, s2])

    alice_skill = await catalog.get_skill(_request_with_user("alice"), "shared")
    bob_skill = await catalog.get_skill(_request_with_user("bob"), "shared")
    assert alice_skill is not None
    assert alice_skill.description == "Restricted"
    assert bob_skill is not None
    assert bob_skill.description == "Public fallback"


@pytest.mark.anyio
async def test_catalog_rejects_duplicate_names_from_one_source() -> "None":
    """A single source returning two skills with the same name fails with ImproperlyConfiguredException."""
    dup_source = _InMemorySource([Skill.build("dup", "One"), Skill.build("dup", "Two")])
    catalog = SkillCatalog([dup_source])

    with pytest.raises(ImproperlyConfiguredException, match="dup"):
        await catalog.list_skills(_request_with_user())


@pytest.mark.anyio
async def test_catalog_rejects_skill_returned_under_another_name() -> "None":
    """A source whose get_skill(req, name) returns a skill with a different name fails."""

    class _LyingSource(_InMemorySource):
        async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
            del request, name
            return Skill.build("wrong", "Wrong name")

    catalog = SkillCatalog([_LyingSource()])
    with pytest.raises(ImproperlyConfiguredException, match="wrong"):
        await catalog.get_skill(_request_with_user(), "expected")


@pytest.mark.anyio
async def test_catalog_serves_inline_content_without_read_file() -> "None":
    """SkillCatalog.read_file returns verified inline content without calling source.read_file."""
    skill = Skill.build("inline", "Inline skill", files={"guide.txt": "hello world"})
    source = _InMemorySource([skill])
    catalog = SkillCatalog([source])
    req = _request_with_user()

    match = await catalog.get_file(req, "skill://inline/guide.txt")
    assert match is not None
    data = await catalog.read_file(req, match)
    assert data == b"hello world"
    assert source.read_calls == []


@pytest.mark.anyio
async def test_catalog_rejects_inline_content_that_does_not_match_digest() -> "None":
    """If inline content is corrupted after construction, verify_skill_file raises SkillIntegrityError."""
    skill = Skill.build("inline", "Inline skill", files={"guide.txt": "hello world"})
    source = _InMemorySource([skill])
    catalog = SkillCatalog([source])
    req = _request_with_user()

    match = await catalog.get_file(req, "skill://inline/guide.txt")
    assert match is not None
    object.__setattr__(match.file, "content", b"corrupted!!")
    with pytest.raises(SkillIntegrityError):
        await catalog.read_file(req, match)


@pytest.mark.anyio
async def test_catalog_reads_lazy_files_from_the_owning_source() -> "None":
    """When SkillFile.content is None, SkillCatalog delegates to the owning source's read_file."""
    payload = b"lazy bytes from db"
    lazy_file = SkillFile(
        relative_path="doc.txt",
        path=None,
        uri="skill://lazy/doc.txt",
        mime_type="text/plain",
        size=len(payload),
        digest=compute_digest(payload),
        content=None,
    )
    skill = Skill.from_content(
        "---\nname: lazy\ndescription: Lazy skill\n---\n",
        files={"doc.txt": lazy_file},
    )
    source = _InMemorySource([skill], lazy_bytes={("lazy", "doc.txt"): payload})
    catalog = SkillCatalog([source])
    req = _request_with_user()

    match = await catalog.get_file(req, "skill://lazy/doc.txt")
    assert match is not None
    assert await catalog.read_file(req, match) == payload
    assert source.read_calls == [("lazy", "doc.txt")]


@pytest.mark.anyio
async def test_catalog_rejects_lazy_bytes_that_do_not_match_manifest() -> "None":
    """When source.read_file returns bytes that drift from the manifest digest/size, SkillIntegrityError is raised."""
    expected = b"expected bytes"
    lazy_file = SkillFile(
        relative_path="doc.txt",
        path=None,
        uri="skill://lazy/doc.txt",
        mime_type="text/plain",
        size=len(expected),
        digest=compute_digest(expected),
        content=None,
    )
    skill = Skill.from_content(
        "---\nname: lazy\ndescription: Lazy skill\n---\n",
        files={"doc.txt": lazy_file},
    )
    source = _InMemorySource([skill], lazy_bytes={("lazy", "doc.txt"): b"drifted bytes!"})
    catalog = SkillCatalog([source])
    req = _request_with_user()

    match = await catalog.get_file(req, "skill://lazy/doc.txt")
    assert match is not None
    with pytest.raises(SkillIntegrityError):
        await catalog.read_file(req, match)


@pytest.mark.anyio
async def test_catalog_filters_every_lookup_per_caller() -> "None":
    """Every catalog lookup method respects per-caller visibility from the source."""
    secret = Skill.build("secret", "Secret skill", files={"a/info.txt": "top secret"})
    source = _InMemorySource([secret], acl={"secret": {"alice"}})
    catalog = SkillCatalog([source])

    alice = _request_with_user("alice")
    bob = _request_with_user("bob")

    assert [s.name for s in await catalog.list_skills(alice)] == ["secret"]
    assert await catalog.list_skills(bob) == []
    assert await catalog.get(alice, "skill://secret/SKILL.md") is not None
    assert await catalog.get(bob, "skill://secret/SKILL.md") is None
    assert await catalog.get_file(alice, "skill://secret/a/info.txt") is not None
    assert await catalog.get_file(bob, "skill://secret/a/info.txt") is None
    assert await catalog.list_directory(alice, "skill://secret") is not None
    assert await catalog.list_directory(bob, "skill://secret") is None
    assert len(await catalog.resource_entries(alice)) == 2
    assert await catalog.resource_entries(bob) == []


def test_hidden_skill_reads_like_an_unknown_uri() -> "None":
    """A skill hidden from a caller returns the exact same -32602 error as an unknown URI."""
    secret = Skill.build("secret", "Secret skill", files={"info.txt": "secret"})
    source = _InMemorySource([secret], acl={"secret": {"alice"}})
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])
    client = TestClient(app=app)

    hidden_resp = mcp_post(
        client,
        "resources/read",
        {"uri": "skill://secret/info.txt"},
        headers={"X-User": "bob"},
    )
    unknown_resp = mcp_post(
        client,
        "resources/read",
        {"uri": "skill://nonexistent/info.txt"},
        headers={"X-User": "bob"},
    )
    hidden_err = hidden_resp.json()["error"]
    unknown_err = unknown_resp.json()["error"]
    assert hidden_err["code"] == unknown_err["code"] == -32602
    assert hidden_err["message"] == unknown_err["message"]
    assert hidden_err["data"] == {"uri": "skill://secret/info.txt"}


def test_source_lookup_error_on_read_is_resource_not_found() -> "None":
    """LookupError raised by source.read_file maps to -32602 Resource not found."""
    payload = b"ephemeral"
    lazy_file = SkillFile(
        relative_path="gone.txt",
        path=None,
        uri="skill://ephemeral/gone.txt",
        mime_type="text/plain",
        size=len(payload),
        digest=compute_digest(payload),
        content=None,
    )
    skill = Skill.from_content(
        "---\nname: ephemeral\ndescription: Ephemeral skill\n---\n",
        files={"gone.txt": lazy_file},
    )
    source = _InMemorySource([skill], read_error=LookupError("row deleted"))
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])
    client = TestClient(app=app)

    resp = mcp_post(client, "resources/read", {"uri": "skill://ephemeral/gone.txt"})
    err = resp.json()["error"]
    assert err["code"] == -32602
    assert err["data"] == {"uri": "skill://ephemeral/gone.txt"}


def test_dynamic_only_config_serves_skills_over_the_wire() -> "None":
    """MCPSkillsConfig(sources=[...]) without paths advertises skills in server/discover and serves them."""
    skill = Skill.build("dynamic-demo", "Dynamic skill", body="# Dynamic\n", files={"extra.txt": "extra"})
    source = _InMemorySource([skill])
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))])
    client = TestClient(app=app)

    discover = mcp_post(client, "server/discover").json()["result"]
    assert "io.modelcontextprotocol/skills" in discover["capabilities"]["extensions"]
    assert len(source.observed_context_requests) == 0

    listed = mcp_post(client, "skills/list").json()["result"]["skills"]
    assert [item["uri"] for item in listed] == ["skill://dynamic-demo/SKILL.md"]
