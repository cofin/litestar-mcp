"""Tests for the filesystem-backed skill catalog."""

import hashlib
import mimetypes
import os
from collections.abc import Iterator  # noqa: TC003
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest
from litestar.exceptions import ImproperlyConfiguredException

from litestar_mcp import LitestarMCP
from litestar_mcp.mcp.config import MCPConfig, MCPSkillsConfig
from litestar_mcp.mcp.skills import (
    SkillCatalog,
    SkillIntegrityError,
    compute_digest,
    parse_frontmatter,
    parse_skill_uri,
)

_DEFAULT_FRONTMATTER = "---\nname: {name}\ndescription: Test skill\n---\n\n# Body\n"


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
    """The digest is ``sha256:`` followed by 64 lowercase hex characters."""
    digest = compute_digest(b"abc")
    assert digest == "sha256:" + hashlib.sha256(b"abc").hexdigest()
    hex_part = digest.split(":", 1)[1]
    assert len(hex_part) == 64
    assert hex_part == hex_part.lower()


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


def test_from_config_builds_manifest_with_digests_and_sizes(tmp_path: "Path") -> "None":
    """The catalog computes correct digests, sizes, and URIs for every file."""
    reference_bytes = b"# Reference\n"
    script_bytes = b"print('hi')\n"
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": reference_bytes, "scripts/run.py": script_bytes},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))

    skill = catalog.get("skill://demo/SKILL.md")
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


def test_from_config_ignores_child_folders_without_skill_md(tmp_path: "Path") -> "None":
    """Child directories that lack ``SKILL.md`` are skipped entirely."""
    _write_skill(tmp_path, "demo", {})
    (tmp_path / "not_a_skill").mkdir()
    (tmp_path / "not_a_skill" / "notes.txt").write_bytes(b"hello")

    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))

    assert [skill.name for skill in catalog.skills] == ["demo"]


def test_from_config_skips_hidden_and_symlinked_entries(tmp_path: "Path") -> "None":
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
    skill = catalog.get("skill://demo/SKILL.md")
    assert skill is not None

    relative_paths = {file.relative_path for file in skill.files}
    assert relative_paths == {"SKILL.md", "reference.md"}


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


def test_mime_types(tmp_path: "Path") -> "None":
    """MIME types are derived from the file extension, with a fallback."""
    _write_skill(
        tmp_path,
        "demo",
        {"data.json": b"{}", "blob.bin": b"\x00\x01"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skill = catalog.get("skill://demo/SKILL.md")
    assert skill is not None

    mime_by_path = {file.relative_path: file.mime_type for file in skill.files}
    assert mime_by_path["SKILL.md"] == "text/markdown"
    assert mime_by_path["data.json"] == "application/json"
    assert mime_by_path["blob.bin"] == "application/octet-stream"


def test_get_and_get_file(tmp_path: "Path") -> "None":
    """``get`` resolves ``SKILL.md`` URIs; ``get_file`` resolves any file URI."""
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": b"# Reference\n", "scripts/run.py": b"print('hi')\n"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))

    skill = catalog.get("skill://demo/SKILL.md")
    assert skill is not None
    assert skill.name == "demo"
    assert catalog.get("skill://demo/reference.md") is None
    assert catalog.get("skill://unknown/SKILL.md") is None

    found = catalog.get_file("skill://demo/scripts/run.py")
    assert found is not None
    found_skill, found_file = found
    assert found_skill.name == "demo"
    assert found_file.relative_path == "scripts/run.py"
    assert catalog.get_file("skill://demo/missing.txt") is None
    assert catalog.get_file("skill://unknown/SKILL.md") is None


def test_list_directory_root_and_nested(tmp_path: "Path") -> "None":
    """``list_directory`` lists files and directories, or ``None`` when invalid."""
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": b"# Reference\n", "scripts/run.py": b"print('hi')\n"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))

    root_entries = catalog.list_directory("skill://demo")
    assert root_entries is not None
    names_and_mime = {entry["name"]: entry["mimeType"] for entry in root_entries}
    assert names_and_mime["SKILL.md"] == "text/markdown"
    assert names_and_mime["reference.md"] == (mimetypes.guess_type("reference.md")[0] or "application/octet-stream")
    assert names_and_mime["scripts"] == "inode/directory"

    nested_entries = catalog.list_directory("skill://demo/scripts")
    assert nested_entries is not None
    assert [entry["uri"] for entry in nested_entries] == ["skill://demo/scripts/run.py"]

    assert catalog.list_directory("skill://demo/missing") is None
    assert catalog.list_directory("skill://demo/SKILL.md") is None


def test_read_file_verifies_digest(tmp_path: "Path") -> "None":
    """``read_file`` returns bytes matching the manifest digest, else raises."""
    reference_bytes = b"# Reference\n"
    _write_skill(tmp_path, "demo", {"reference.md": reference_bytes})
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skill = catalog.get("skill://demo/SKILL.md")
    assert skill is not None
    reference_file = next(file for file in skill.files if file.relative_path == "reference.md")

    assert catalog.read_file(reference_file) == reference_bytes

    reference_file.path.write_bytes(b"tampered")
    with pytest.raises(SkillIntegrityError):
        catalog.read_file(reference_file)


def test_manifest_and_catalog_ordering_is_deterministic(
    tmp_path: "Path",
    monkeypatch: "pytest.MonkeyPatch",
) -> "None":
    """Resources are ordered by path; the catalog sorts skills regardless of input order.

    ``os.walk`` is monkeypatched to hand back each directory's filenames in reverse
    order, so the ascending order asserted below can only come from the explicit
    sort in ``load_skill`` and not from incidental filesystem iteration order.
    """
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

    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    skill = catalog.get("skill://demo/SKILL.md")
    assert skill is not None
    assert [file.relative_path for file in skill.files] == ["SKILL.md", "reference.md", "scripts/run.py"]

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _write_skill(root_a, "alpha", {})
    _write_skill(root_b, "beta", {})
    ordered = SkillCatalog.from_config(MCPSkillsConfig(paths=[root_a, root_b]))
    alpha_skill = ordered.get("skill://alpha/SKILL.md")
    beta_skill = ordered.get("skill://beta/SKILL.md")
    assert alpha_skill is not None
    assert beta_skill is not None

    reversed_catalog = SkillCatalog(skills=[beta_skill, alpha_skill])
    assert reversed_catalog.skills[0].name == "alpha"
    assert reversed_catalog.skills[1].name == "beta"


def test_list_directory_sorts_by_emitted_name(tmp_path: "Path") -> "None":
    """``list_directory`` sorts by the emitted ``name`` value, not the bare path segment."""
    _write_skill(
        tmp_path,
        "demo",
        {"a/zeta.txt": b"zeta", "a/m/x.txt": b"x"},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))

    entries = catalog.list_directory("skill://demo/a")
    assert entries is not None
    assert [entry["name"] for entry in entries] == ["a/zeta.txt", "m"]


def test_resource_entries_covers_every_file_with_size(tmp_path: "Path") -> "None":
    """``resource_entries`` lists every file once, each with a size, and scopes ``description``."""
    reference_bytes = b"# Reference\n"
    script_bytes = b"print('hi')\n"
    _write_skill(
        tmp_path,
        "demo",
        {"reference.md": reference_bytes, "scripts/run.py": script_bytes},
    )
    catalog = SkillCatalog.from_config(MCPSkillsConfig(paths=[tmp_path]))
    entries = catalog.resource_entries()

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


def test_plugin_builds_catalog_only_when_configured(tmp_path: "Path") -> "None":
    """The plugin builds a catalog only when ``MCPConfig.skills`` is set."""
    assert LitestarMCP().skill_catalog is None

    _write_skill(tmp_path, "demo", {})
    plugin = LitestarMCP(MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path])))
    catalog = plugin.skill_catalog
    assert catalog is not None
    assert catalog.skills[0].name == "demo"

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
