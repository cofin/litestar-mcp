"""Tests for the filesystem-backed skill source and skill catalog."""

import hashlib
import mimetypes
import os
from collections.abc import Iterator, Sequence  # noqa: TC003
from pathlib import Path  # noqa: TC003
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from litestar import Litestar, Request, get
from litestar.datastructures import State
from litestar.di import Provide
from litestar.exceptions import ImproperlyConfiguredException
from litestar.testing import TestClient

from litestar_mcp import FilesystemSkillSource, LitestarMCP
from litestar_mcp.mcp.config import MCPConfig, MCPSkillsConfig
from litestar_mcp.mcp.skills import (
    Skill,
    SkillCatalog,
    SkillFile,
    SkillIntegrityError,
    compute_digest,
    parse_frontmatter,
    parse_skill_uri,
)

_DEFAULT_FRONTMATTER = "---\nname: {name}\ndescription: Test skill\n---\n\n# Body\n"


def _dummy_request() -> "Request[Any, Any, Any]":
    """Return a minimal Request stub for source/catalog unit calls."""
    return MagicMock(spec=Request)


def _write_skill(
    root: "Path",
    name: "str",
    files: "dict[str, bytes]",
    *,
    frontmatter: "str | None" = None,
) -> "Path":
    """Create ``root/name`` with a ``SKILL.md`` and the given extra files."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_text = frontmatter if frontmatter is not None else _DEFAULT_FRONTMATTER.format(name=name)
    (skill_dir / "SKILL.md").write_bytes(skill_md_text.encode())
    for relative_path, data in files.items():
        file_path = skill_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
    return skill_dir


def test_compute_digest_is_prefixed_lowercase_sha256() -> "None":
    """The digest is ``sha256:`` followed by the hex SHA-256 of the data."""
    assert compute_digest(b"abc") == "sha256:" + hashlib.sha256(b"abc").hexdigest()


def test_parse_frontmatter_returns_mapping_verbatim() -> "None":
    """Extra frontmatter keys survive parsing unchanged; body text is ignored."""
    text = "---\nname: demo\ndescription: Test skill\nversion: 2\ntags: [a, b]\n---\n\n# Body\n"
    frontmatter = parse_frontmatter(text)
    assert frontmatter["name"] == "demo"
    assert frontmatter["description"] == "Test skill"
    assert frontmatter["version"] == 2
    assert frontmatter["tags"] == ["a", "b"]


def test_parse_frontmatter_rejects_missing_fence() -> "None":
    """Text without a leading ``---`` fence is rejected."""
    with pytest.raises(ImproperlyConfiguredException):
        parse_frontmatter("name: demo\ndescription: Test skill\n")


def test_parse_frontmatter_rejects_non_mapping() -> "None":
    """A frontmatter block that does not parse to a mapping is rejected."""
    with pytest.raises(ImproperlyConfiguredException):
        parse_frontmatter("---\n- a\n- b\n---\n\n# Body\n")


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("skill://demo/SKILL.md", ("demo", "SKILL.md")),
        ("skill://demo", ("demo", "")),
        ("skill://demo/a/b.txt", ("demo", "a/b.txt")),
        ("litestar://x", None),
        ("skill://", None),
        ("skill://demo/../x", None),
        ("skill://demo/", None),
        ("skill://./x", None),
        ("skill://../x", None),
        ("skill://demo/./x", None),
        ("skill://demo/..", None),
    ],
)
def test_parse_skill_uri(uri: "str", expected: "tuple[str, str] | None") -> "None":
    """``parse_skill_uri`` splits a skill URI into ``(name, relative_path)``."""
    assert parse_skill_uri(uri) == expected


@pytest.mark.anyio
async def test_from_config_builds_manifest_with_digests_and_sizes(tmp_path: "Path") -> "None":
    """The catalog computes correct digests, sizes, and URIs for every file."""
    reference_bytes = b"# Reference\n"
    script_bytes = b"print('hi')\n"
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": reference_bytes, "scripts/run.py": script_bytes},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    req = _dummy_request()

    skill = await catalog.get(req, "skill://demo/SKILL.md")
    assert skill is not None
    assert skill.uri == "skill://demo/SKILL.md"
    assert skill.frontmatter["name"] == "demo"

    entry = skill.to_entry()
    resources = entry["resources"]
    assert [item["uri"] for item in resources] == [
        "skill://demo/SKILL.md",
        "skill://demo/reference.md",
        "skill://demo/scripts/run.py",
    ]

    skill_md_bytes = (tmp_path / "demo" / "SKILL.md").read_bytes()
    expected_sizes = {
        "skill://demo/SKILL.md": len(skill_md_bytes),
        "skill://demo/reference.md": len(reference_bytes),
        "skill://demo/scripts/run.py": len(script_bytes),
    }
    expected_digests = {
        "skill://demo/SKILL.md": compute_digest(skill_md_bytes),
        "skill://demo/reference.md": compute_digest(reference_bytes),
        "skill://demo/scripts/run.py": compute_digest(script_bytes),
    }
    for item in resources:
        assert item["size"] == expected_sizes[item["uri"]]
        assert item["digest"] == expected_digests[item["uri"]]


@pytest.mark.anyio
async def test_from_config_ignores_child_folders_without_skill_md(tmp_path: "Path") -> "None":
    """Child directories that lack ``SKILL.md`` are skipped entirely."""
    _write_skill(tmp_path, "demo", {})
    (tmp_path / "not_a_skill").mkdir()
    (tmp_path / "not_a_skill" / "notes.txt").write_bytes(b"hello")

    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skills = await catalog.list_skills(_dummy_request())

    assert [skill.name for skill in skills] == ["demo"]


@pytest.mark.anyio
async def test_from_config_skips_hidden_and_symlinked_entries(tmp_path: "Path") -> "None":
    """Hidden files/directories and symlinks never appear in the manifest."""
    skill_dir = _write_skill(tmp_path, "demo", {"reference.md": b"# Reference\n"})
    hidden_dir = skill_dir / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "x").write_bytes(b"secret")
    (skill_dir / ".DS_Store").write_bytes(b"junk")

    outside_file = tmp_path / "outside.txt"
    outside_file.write_bytes(b"outside file")
    (skill_dir / "linked_file.txt").symlink_to(outside_file)

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "child.txt").write_bytes(b"outside dir child")
    (skill_dir / "linked_dir").symlink_to(outside_dir, target_is_directory=True)

    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skill = await catalog.get(_dummy_request(), "skill://demo/SKILL.md")
    assert skill is not None

    relative_paths = {file.relative_path for file in skill.files}
    assert relative_paths == {"SKILL.md", "reference.md"}


@pytest.mark.anyio
async def test_from_config_skips_symlinked_skill_folder(tmp_path: "Path") -> "None":
    """A skill folder that is itself a symlink is never loaded."""
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_skill(outside, "demo", {"reference.md": b"# Reference\n"})
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    (skills_root / "demo").symlink_to(outside / "demo", target_is_directory=True)

    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[skills_root]))

    assert await catalog.list_skills(_dummy_request()) == []


def test_from_config_rejects_symlinked_skill_md(tmp_path: "Path") -> "None":
    """A symlinked ``SKILL.md`` is rejected rather than advertised as an unreadable file."""
    target = tmp_path / "shared_skill.md"
    target.write_bytes(_DEFAULT_FRONTMATTER.format(name="demo").encode())
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").symlink_to(target)

    with pytest.raises(ImproperlyConfiguredException, match="demo"):
        SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))


@pytest.mark.anyio
async def test_to_entry_frontmatter_is_a_copy(tmp_path: "Path") -> "None":
    """A caller mutating the returned frontmatter cannot corrupt the catalog."""
    _write_skill(tmp_path, "demo", {})
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skill = await catalog.get(_dummy_request(), "skill://demo/SKILL.md")
    assert skill is not None

    entry = skill.to_entry()
    entry["frontmatter"]["description"] = "tampered"
    entry["frontmatter"]["injected"] = True

    assert skill.frontmatter["description"] == "Test skill"
    assert "injected" not in skill.frontmatter
    assert skill.to_entry()["frontmatter"] == {"name": "demo", "description": "Test skill"}


def test_from_config_rejects_name_mismatch(tmp_path: "Path") -> "None":
    """A skill whose frontmatter ``name`` differs from the folder name is rejected."""
    _write_skill(
        tmp_path,
        "demo",
        {},
        frontmatter="---\nname: other\ndescription: Test skill\n---\n\n# Body\n",
    )
    with pytest.raises(ImproperlyConfiguredException):
        SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))


def test_from_config_rejects_missing_description(tmp_path: "Path") -> "None":
    """A skill whose frontmatter lacks a non-empty ``description`` is rejected."""
    _write_skill(
        tmp_path,
        "demo",
        {},
        frontmatter="---\nname: demo\n---\n\n# Body\n",
    )
    with pytest.raises(ImproperlyConfiguredException):
        SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))


def test_from_config_rejects_duplicate_skill_names_across_paths(tmp_path: "Path") -> "None":
    """The same skill name found under two configured paths is rejected."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _write_skill(root_a, "demo", {})
    _write_skill(root_b, "demo", {})

    with pytest.raises(ImproperlyConfiguredException):
        SkillCatalog.from_config(MCPSkillsConfig(paths=[root_a, root_b]))


def test_from_config_rejects_missing_directory(tmp_path: "Path") -> "None":
    """A configured path that is not a directory is rejected."""
    missing = tmp_path / "does_not_exist"
    with pytest.raises(ImproperlyConfiguredException):
        SkillCatalog.from_config(MCPSkillsConfig(paths=[missing]))


def test_from_config_enforces_file_and_byte_limits(tmp_path: "Path") -> "None":
    """Skills that exceed the file-count or byte-size limits are rejected."""
    root_a = tmp_path / "a"
    root_a.mkdir()
    _write_skill(root_a, "demo", {"reference.md": b"# Reference\n"})
    with pytest.raises(ImproperlyConfiguredException, match="demo"):
        SkillCatalog.from_config(MCPSkillsConfig(paths=[root_a], max_files_per_skill=1))

    root_b = tmp_path / "b"
    root_b.mkdir()
    _write_skill(root_b, "demo", {"big.txt": b"x" * 20})
    with pytest.raises(ImproperlyConfiguredException, match="demo"):
        SkillCatalog.from_config(MCPSkillsConfig(paths=[root_b], max_bytes_per_skill=10))


@pytest.mark.anyio
async def test_mime_types(tmp_path: "Path") -> "None":
    """MIME types are derived from the file extension, with a fallback."""
    _write_skill(
        tmp_path,
        "demo",
        {"data.json": b"{}", "blob.bin": b"\x00\x01"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skill = await catalog.get(_dummy_request(), "skill://demo/SKILL.md")
    assert skill is not None

    mime_by_path = {file.relative_path: file.mime_type for file in skill.files}
    assert mime_by_path["SKILL.md"] == "text/markdown"
    assert mime_by_path["data.json"] == "application/json"
    assert mime_by_path["blob.bin"] == "application/octet-stream"


@pytest.mark.anyio
async def test_get_and_get_file(tmp_path: "Path") -> "None":
    """``get`` resolves ``SKILL.md`` URIs; ``get_file`` resolves any file URI."""
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": b"# Reference\n", "scripts/run.py": b"print('hi')\n"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    req = _dummy_request()

    skill = await catalog.get(req, "skill://demo/SKILL.md")
    assert skill is not None
    assert skill.name == "demo"
    assert await catalog.get(req, "skill://demo/reference.md") is None
    assert await catalog.get(req, "skill://unknown/SKILL.md") is None

    found = await catalog.get_file(req, "skill://demo/scripts/run.py")
    assert found is not None
    assert found.skill.name == "demo"
    assert found.file.relative_path == "scripts/run.py"
    assert await catalog.get_file(req, "skill://demo/missing.txt") is None
    assert await catalog.get_file(req, "skill://unknown/SKILL.md") is None


@pytest.mark.anyio
async def test_list_directory_root_and_nested(tmp_path: "Path") -> "None":
    """``list_directory`` lists files and directories, or ``None`` when invalid."""
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": b"# Reference\n", "scripts/run.py": b"print('hi')\n"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    req = _dummy_request()

    root_entries = await catalog.list_directory(req, "skill://demo")
    assert root_entries is not None
    names_and_mime = {entry["name"]: entry["mimeType"] for entry in root_entries}
    assert names_and_mime["SKILL.md"] == "text/markdown"
    assert names_and_mime["reference.md"] == (mimetypes.guess_type("reference.md")[0] or "application/octet-stream")
    assert names_and_mime["scripts"] == "inode/directory"

    nested_entries = await catalog.list_directory(req, "skill://demo/scripts")
    assert nested_entries is not None
    assert [entry["uri"] for entry in nested_entries] == ["skill://demo/scripts/run.py"]

    assert await catalog.list_directory(req, "skill://demo/missing") is None
    assert await catalog.list_directory(req, "skill://demo/SKILL.md") is None


@pytest.mark.anyio
async def test_read_file_verifies_digest(tmp_path: "Path") -> "None":
    """``read_file`` returns bytes matching the manifest digest, else raises."""
    reference_bytes = b"# Reference\n"
    _write_skill(tmp_path, "demo", {"reference.md": reference_bytes})
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    req = _dummy_request()
    match = await catalog.get_file(req, "skill://demo/reference.md")
    assert match is not None
    assert match.file.path is not None

    assert await catalog.read_file(req, match) == reference_bytes

    match.file.path.write_bytes(b"tampered")
    with pytest.raises(SkillIntegrityError):
        await catalog.read_file(req, match)


@pytest.mark.anyio
async def test_manifest_and_catalog_ordering_is_deterministic(
    tmp_path: "Path",
    monkeypatch: "pytest.MonkeyPatch",
) -> "None":
    """Resources are ordered by path; the catalog sorts skills regardless of input order."""
    reference_bytes = b"# Reference\n"
    script_bytes = b"print('hi')\n"
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": reference_bytes, "scripts/run.py": script_bytes},
    )

    real_walk = os.walk

    def reversed_walk(*args: "Any", **kwargs: "Any") -> "Iterator[tuple[str, list[str], list[str]]]":
        for dirpath, dirnames, filenames in real_walk(*args, **kwargs):
            yield dirpath, dirnames, list(reversed(filenames))

    monkeypatch.setattr(os, "walk", reversed_walk)

    req = _dummy_request()
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skill = await catalog.get(req, "skill://demo/SKILL.md")
    assert skill is not None
    assert [file.relative_path for file in skill.files] == ["SKILL.md", "reference.md", "scripts/run.py"]

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _write_skill(root_a, "alpha", {})
    _write_skill(root_b, "beta", {})
    source_b = FilesystemSkillSource([root_b])
    source_a = FilesystemSkillSource([root_a])

    reversed_catalog = SkillCatalog(sources=[source_b, source_a])
    listed = await reversed_catalog.list_skills(req)
    assert [item.name for item in listed] == ["alpha", "beta"]


@pytest.mark.anyio
async def test_list_directory_sorts_by_emitted_name(tmp_path: "Path") -> "None":
    """``list_directory`` sorts by the emitted ``name`` value, not the bare path segment."""
    _write_skill(
        tmp_path,
        "demo",
        {"a/zeta.txt": b"zeta", "a/m/x.txt": b"x"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))

    entries = await catalog.list_directory(_dummy_request(), "skill://demo/a")
    assert entries is not None
    assert [entry["name"] for entry in entries] == ["a/zeta.txt", "m"]


@pytest.mark.anyio
async def test_resource_entries_covers_every_file_with_size(tmp_path: "Path") -> "None":
    """``resource_entries`` lists every file once, each with a size, and scopes ``description``."""
    reference_bytes = b"# Reference\n"
    script_bytes = b"print('hi')\n"
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": reference_bytes, "scripts/run.py": script_bytes},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    entries = await catalog.resource_entries(_dummy_request())

    uris = [entry["uri"] for entry in entries]
    assert sorted(uris) == [
        "skill://demo/SKILL.md",
        "skill://demo/reference.md",
        "skill://demo/scripts/run.py",
    ]
    assert len(uris) == len(set(uris))

    for entry in entries:
        assert {"uri", "name", "mimeType", "size"} <= set(entry)
        if entry["uri"] == "skill://demo/SKILL.md":
            assert entry["description"] == "Test skill"
        else:
            assert "description" not in entry


@pytest.mark.anyio
async def test_plugin_builds_catalog_only_when_configured(tmp_path: "Path") -> "None":
    """The plugin builds a catalog only when ``MCPConfig.skills`` is set."""
    assert LitestarMCP().skill_catalog is None

    _write_skill(tmp_path, "demo", {})
    plugin = LitestarMCP(MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path])))
    catalog = plugin.skill_catalog
    assert catalog is not None
    skills = await catalog.list_skills(_dummy_request())
    assert skills[0].name == "demo"

    invalid_root = tmp_path / "invalid"
    invalid_root.mkdir()
    _write_skill(
        invalid_root,
        "bad",
        {},
        frontmatter="---\nname: mismatch\ndescription: Test skill\n---\n\n# Body\n",
    )
    with pytest.raises(ImproperlyConfiguredException):
        LitestarMCP(MCPConfig(skills=MCPSkillsConfig(paths=[invalid_root])))


class _RecordingSkillSource:
    """In-memory SkillSource that records requests passed from SkillCatalog."""

    def __init__(
        self, skills: "Sequence[Skill]", *, lazy_bytes: "dict[tuple[str, str], bytes] | None" = None
    ) -> "None":
        self._skills = tuple(skills)
        self._lazy_bytes = dict(lazy_bytes or {})
        self.received_requests: list[Any] = []

    async def list_skills(self, request: "Request[Any, Any, Any]", /) -> "Sequence[Skill]":
        self.received_requests.append(request)
        marker = request.app.state.get("marker") if request.app is not None else None
        if marker is not None:
            return [
                Skill.build(
                    f"{skill.name}-{marker}",
                    skill.description,
                    tags=list(skill.tags),
                    title=skill.title,
                )
                for skill in self._skills
            ]
        _ = (
            request.headers,
            request.query_params,
            request.method,
            request.url.path,
            request.user,
            request.auth,
            request.state,
            request.scope,
        )
        return self._skills

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
        self.received_requests.append(request)
        _ = (
            request.headers,
            request.query_params,
            request.method,
            request.url.path,
            request.user,
            request.auth,
            request.state,
            request.scope,
            request.app,
        )
        return next((skill for skill in self._skills if skill.name == name), None)

    async def read_file(self, request: "Request[Any, Any, Any]", skill: "Skill", file: "SkillFile", /) -> "bytes":
        self.received_requests.append(request)
        key = (skill.name, file.relative_path)
        if key in self._lazy_bytes:
            return self._lazy_bytes[key]
        return file.content or b""


@pytest.mark.anyio
async def test_catalog_request_optional_all_methods() -> "None":
    """All seven SkillCatalog async methods support omitted, None, keyword, and duck-typed requests."""
    ref_bytes = b"reference payload"
    lazy_ref = SkillFile(
        relative_path="reference.md",
        path=None,
        uri="skill://demo/reference.md",
        mime_type="text/markdown",
        size=len(ref_bytes),
        digest=compute_digest(ref_bytes),
        content=None,
    )
    demo = Skill.build("demo", "Demo skill", files={"reference.md": lazy_ref})
    source = _RecordingSkillSource([demo], lazy_bytes={("demo", "reference.md"): ref_bytes})
    catalog = SkillCatalog([source])

    assert [s.name for s in await catalog.list_skills()] == ["demo"]
    assert isinstance(source.received_requests[-1], Request)

    assert (await catalog.get_skill("demo")) is demo
    assert (await catalog.get_skill(None, "demo")) is demo
    assert (await catalog.get_skill("demo", request=None)) is demo
    assert (await catalog.get_skill(name="demo")) is demo
    assert (await catalog.get("skill://demo")) is demo

    duck_req = SimpleNamespace(
        headers={},
        query_params={},
        method="GET",
        url=SimpleNamespace(path="/"),
        user=None,
        auth=None,
        state={},
        scope={},
        app=None,
    )
    duck_req_typed = cast("Request[Any, Any, Any]", duck_req)
    assert (await catalog.get(duck_req_typed, "skill://demo")) is demo
    assert source.received_requests[-1] is duck_req_typed

    match = await catalog.get_file("skill://demo/reference.md")
    assert match is not None
    assert await catalog.read_file(match) == ref_bytes

    entries = await catalog.list_directory("skill://demo")
    assert entries is not None
    assert [entry["name"] for entry in entries] == ["SKILL.md", "reference.md"]

    resources = await catalog.resource_entries()
    assert len(resources) == 2


@pytest.mark.anyio
async def test_catalog_list_skills_tags_and_query_without_request() -> "None":
    """SkillCatalog.list_skills supports tags and query filters with no request argument."""
    alpha = Skill.build("alpha", "Database index guide", tags=["ops", "sql"], title="SQL Tuning")
    beta = Skill.build("beta", "Documentation style guide", tags=["docs"])
    catalog = SkillCatalog([_RecordingSkillSource([alpha, beta])])

    filtered = await catalog.list_skills(tags=["ops"], query="tuning")
    assert filtered == [alpha]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invoker",
    [
        lambda catalog, req: catalog.get_skill(req),
        lambda catalog, _req: catalog.get_skill(None),
        lambda catalog, _req: catalog.get_skill(),
        lambda catalog, req: catalog.get(request=req),
        lambda catalog, req: catalog.read_file(req),
    ],
)
async def test_catalog_missing_target_raises_type_error(invoker: "Any") -> "None":
    """Omitting the target argument on SkillCatalog lookup/read methods raises TypeError."""
    catalog = SkillCatalog([])
    req = _dummy_request()
    with pytest.raises(TypeError):
        await invoker(catalog, req)


def test_app_di_injects_skill_catalog_with_app_state() -> "None":
    """Litestar DI injects SkillCatalog and bind_app exposes request.app.state to sources."""
    source = _RecordingSkillSource([Skill.build("alpha", "Alpha")])

    @get("/skills")
    async def list_app_skills(skill_catalog: SkillCatalog) -> "dict[str, list[str]]":
        skills = await skill_catalog.list_skills()
        return {"names": [skill.name for skill in skills]}

    app = Litestar(
        route_handlers=[list_app_skills],
        state=State({"marker": "live"}),
        plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[source])))],
    )
    with TestClient(app=app) as client:
        response = client.get("/skills")
        assert response.status_code == 200
        assert response.json() == {"names": ["alpha-live"]}


def test_app_di_skills_none_injects_none() -> "None":
    """When MCPConfig(skills=None), Litestar DI supplies None for SkillCatalog | None."""

    @get("/maybe-skills")
    async def check_catalog(skill_catalog: "SkillCatalog | None") -> "dict[str, bool]":
        return {"is_none": skill_catalog is None}

    app = Litestar(
        route_handlers=[check_catalog],
        plugins=[LitestarMCP(MCPConfig(skills=None))],
    )
    with TestClient(app=app) as client:
        response = client.get("/maybe-skills")
        assert response.status_code == 200
        assert response.json() == {"is_none": True}


def test_app_di_setdefault_preserves_user_override() -> "None":
    """User-supplied skill_catalog dependency takes precedence over LitestarMCP default."""
    plugin_source = _RecordingSkillSource([Skill.build("plugin-skill", "Plugin")])
    custom_catalog = SkillCatalog([_RecordingSkillSource([Skill.build("custom-skill", "Custom")])])

    def provide_custom_catalog() -> "SkillCatalog":
        return custom_catalog

    @get("/skills")
    async def list_app_skills(skill_catalog: SkillCatalog) -> "dict[str, list[str]]":
        skills = await skill_catalog.list_skills()
        return {"names": [skill.name for skill in skills]}

    app = Litestar(
        route_handlers=[list_app_skills],
        dependencies={"skill_catalog": Provide(provide_custom_catalog, sync_to_thread=False)},
        plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(sources=[plugin_source])))],
    )
    with TestClient(app=app) as client:
        response = client.get("/skills")
        assert response.status_code == 200
        assert response.json() == {"names": ["custom-skill"]}
