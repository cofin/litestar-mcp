"""Skills over MCP extension tests: advertisement and wire methods."""

import base64
from pathlib import Path  # noqa: TC003
from typing import Any, cast

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, MCPSkillsConfig
from litestar_mcp.mcp.service import SKILLS_EXTENSION, TASKS_EXTENSION
from tests.unit.conftest import mcp_envelope, mcp_post


def _write_skill(root: "Path", name: "str", description: "str", extra_files: "dict[str, str] | None" = None) -> "None":
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")
    for relative_path, content in (extra_files or {}).items():
        file_path = skill_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)


_LOGO_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_TODO_BYTES = b"- todo\n"


def _make_skills_app(
    root: "Path",
    *,
    tasks: "bool" = False,
    binary_asset: "bool" = False,
    max_blob_bytes: "int | None" = None,
    **skills_kwargs: "Any",
) -> "Litestar":
    _write_skill(root, "alpha", "Alpha skill.", {"notes/todo.md": _TODO_BYTES.decode()})
    if binary_asset:
        assets_dir = root / "alpha" / "assets"
        assets_dir.mkdir()
        (assets_dir / "logo.png").write_bytes(_LOGO_BYTES)
    _write_skill(root, "beta", "Beta skill.")
    config_kwargs: dict[str, Any] = {"tasks": tasks, "skills": MCPSkillsConfig(paths=[root], **skills_kwargs)}
    if max_blob_bytes is not None:
        config_kwargs["max_blob_bytes"] = max_blob_bytes
    return Litestar(plugins=[LitestarMCP(MCPConfig(**config_kwargs))])


def _rpc(client: "TestClient[Any]", method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
    return cast("dict[str, Any]", mcp_post(client, method, params or {}).json())


def test_discovery_advertises_skills_only_when_enabled(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        enabled = _rpc(client, "server/discover")["result"]
    with TestClient(app=Litestar(plugins=[LitestarMCP()])) as client:
        disabled = _rpc(client, "server/discover")["result"]

    assert enabled["capabilities"]["extensions"] == {SKILLS_EXTENSION: {"directoryRead": True}}
    assert "resources" in enabled["capabilities"]
    assert "extensions" not in disabled["capabilities"]

    both_root = tmp_path / "both"
    both_root.mkdir()
    with TestClient(app=_make_skills_app(both_root, tasks=True)) as client:
        both = _rpc(client, "server/discover")["result"]
    assert set(both["capabilities"]["extensions"]) == {TASKS_EXTENSION, SKILLS_EXTENSION}


def test_discovery_reports_directory_read_false(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path, directory_read=False)) as client:
        result = _rpc(client, "server/discover")["result"]

    assert result["capabilities"]["extensions"] == {SKILLS_EXTENSION: {"directoryRead": False}}


def test_skills_list_returns_entries_with_cache_fields(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        result = _rpc(client, "skills/list")["result"]

    assert [skill["uri"] for skill in result["skills"]] == [
        "skill://alpha/SKILL.md",
        "skill://beta/SKILL.md",
    ]
    alpha, beta = result["skills"]
    assert set(alpha) == {"uri", "frontmatter", "resources"}
    assert set(beta) == {"uri", "frontmatter", "resources"}
    assert len(alpha["resources"]) == 2
    assert len(beta["resources"]) == 1
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"


def test_skills_list_paginates_atomic_entries(tmp_path: "Path") -> "None":
    _write_skill(tmp_path, "alpha", "Alpha skill.", {"notes/todo.md": "- todo\n"})
    _write_skill(tmp_path, "beta", "Beta skill.")
    app = Litestar(plugins=[LitestarMCP(MCPConfig(list_page_size=1, skills=MCPSkillsConfig(paths=[tmp_path])))])

    with TestClient(app=app) as client:
        first = _rpc(client, "skills/list")["result"]
        second = _rpc(client, "skills/list", {"cursor": first["nextCursor"]})["result"]

    assert [skill["uri"] for skill in first["skills"]] == ["skill://alpha/SKILL.md"]
    assert [skill["uri"] for skill in second["skills"]] == ["skill://beta/SKILL.md"]
    assert "nextCursor" not in second


def test_skills_get_returns_skill_with_cache_fields(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        result = _rpc(client, "skills/get", {"uri": "skill://alpha/SKILL.md"})["result"]

    assert result["skill"]["frontmatter"]["name"] == "alpha"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"


def test_skills_get_unknown_uri_is_invalid_params(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        response = _rpc(client, "skills/get", {"uri": "skill://nope/SKILL.md"})

    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["uri"] == "skill://nope/SKILL.md"


def test_directory_read_lists_direct_children(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        root_listing = _rpc(client, "resources/directory/read", {"uri": "skill://alpha"})["result"]
        notes_listing = _rpc(client, "resources/directory/read", {"uri": "skill://alpha/notes"})["result"]
        skills_list_result = _rpc(client, "skills/list")["result"]

    root_entries = {entry["uri"]: entry for entry in root_listing["resources"]}
    skill_md = root_entries["skill://alpha/SKILL.md"]
    assert skill_md["mimeType"] == "text/markdown"
    assert skill_md["name"] == "SKILL.md"
    assert skill_md["size"] == len((tmp_path / "alpha" / "SKILL.md").read_bytes())
    notes_dir = root_entries["skill://alpha/notes"]
    assert notes_dir["mimeType"] == "inode/directory"
    assert notes_dir["name"] == "notes"

    notes_entries = {entry["uri"]: entry for entry in notes_listing["resources"]}
    assert "skill://alpha/notes/todo.md" in notes_entries

    # resources/directory/read is deliberately absent from _CACHEABLE_METHODS: the spec
    # only requires cache fields on list and get, so a directory listing must not carry
    # them even though a sibling list method (skills/list) does.
    assert "ttlMs" not in root_listing
    assert "cacheScope" not in root_listing
    assert skills_list_result["ttlMs"] == 0
    assert skills_list_result["cacheScope"] == "private"


def test_directory_read_rejects_trailing_slash_and_unknown(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        trailing = _rpc(client, "resources/directory/read", {"uri": "skill://alpha/"})
        unknown = _rpc(client, "resources/directory/read", {"uri": "skill://alpha/missing"})
        not_a_directory = _rpc(client, "resources/directory/read", {"uri": "skill://alpha/SKILL.md"})

    assert trailing["error"]["code"] == -32602
    assert unknown["error"]["code"] == -32602
    assert not_a_directory["error"]["code"] == -32602


def test_directory_read_disabled_is_method_not_found(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path, directory_read=False)) as client:
        response = mcp_post(client, "resources/directory/read", {"uri": "skill://alpha"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


def test_skill_methods_unavailable_when_skills_disabled() -> "None":
    with TestClient(app=Litestar(plugins=[LitestarMCP()])) as client:
        for method, params in (
            ("skills/list", {}),
            ("skills/get", {"uri": "skill://alpha/SKILL.md"}),
            ("resources/directory/read", {"uri": "skill://alpha"}),
        ):
            response = _rpc(client, method, params)
            assert response["error"]["code"] == -32601


def test_skills_get_requires_matching_name_header(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        body, headers = mcp_envelope("skills/get", {"uri": "skill://alpha/SKILL.md"})
        headers["Mcp-Name"] = "skill://beta/SKILL.md"
        response = client.post("/mcp", json=body, headers=headers)

    assert response.json()["error"]["code"] == -32020


def test_directory_read_requires_name_header(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        body, headers = mcp_envelope("resources/directory/read", {"uri": "skill://alpha"})
        headers["Mcp-Name"] = "skill://beta"
        response = client.post("/mcp", json=body, headers=headers)

    assert response.json()["error"]["code"] == -32020


def test_resources_list_includes_skill_files(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path, binary_asset=True)) as client:
        result = _rpc(client, "resources/list")["result"]

    assert result["resources"][0]["uri"] == "litestar://openapi"
    resources = {resource["uri"]: resource for resource in result["resources"]}

    skill_md = resources["skill://alpha/SKILL.md"]
    assert skill_md["name"] == "SKILL.md"
    assert skill_md["description"] == "Alpha skill."
    assert skill_md["mimeType"] == "text/markdown"
    assert skill_md["size"] == len((tmp_path / "alpha" / "SKILL.md").read_bytes())

    todo = resources["skill://alpha/notes/todo.md"]
    assert todo["name"] == "notes/todo.md"
    assert todo["size"] == len(_TODO_BYTES)

    logo = resources["skill://alpha/assets/logo.png"]
    assert logo["mimeType"] == "image/png"
    assert logo["size"] == len(_LOGO_BYTES)

    assert "skill://beta/SKILL.md" in resources


def test_resources_read_skill_markdown_as_text(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        result = _rpc(client, "resources/read", {"uri": "skill://alpha/SKILL.md"})["result"]

    content = result["contents"][0]
    assert content["text"] == (tmp_path / "alpha" / "SKILL.md").read_text()
    assert content["mimeType"] == "text/markdown"
    assert "blob" not in content
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"


def test_resources_read_binary_skill_file_as_blob(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path, binary_asset=True)) as client:
        result = _rpc(client, "resources/read", {"uri": "skill://alpha/assets/logo.png"})["result"]

    content = result["contents"][0]
    assert base64.b64decode(content["blob"]) == _LOGO_BYTES
    assert content["mimeType"] == "image/png"
    assert "text" not in content


def test_resources_read_blob_over_max_blob_bytes_is_internal_error(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path, binary_asset=True, max_blob_bytes=8)) as client:
        response = _rpc(client, "resources/read", {"uri": "skill://alpha/assets/logo.png"})

    assert response["error"]["code"] == -32603
    assert response["error"]["data"]["error"] == "ValueError"


def test_resources_read_refuses_oversized_blob_before_reading_the_file(tmp_path: "Path") -> "None":
    """The manifest size decides the refusal, so an oversized file is never read from disk."""
    with TestClient(app=_make_skills_app(tmp_path, binary_asset=True, max_blob_bytes=8)) as client:
        (tmp_path / "alpha" / "assets" / "logo.png").unlink()
        response = _rpc(client, "resources/read", {"uri": "skill://alpha/assets/logo.png"})

    assert response["error"]["code"] == -32603
    assert response["error"]["data"]["error"] == "ValueError"


def test_resources_read_serves_text_larger_than_max_blob_bytes(tmp_path: "Path") -> "None":
    """A text media type is never capped up front; only a blob fallback is bounded."""
    with TestClient(app=_make_skills_app(tmp_path, max_blob_bytes=1)) as client:
        result = _rpc(client, "resources/read", {"uri": "skill://alpha/notes/todo.md"})["result"]

    assert result["contents"][0]["text"] == _TODO_BYTES.decode()


def test_resources_read_unknown_skill_file_is_resource_not_found(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        missing_file = _rpc(client, "resources/read", {"uri": "skill://alpha/missing.md"})
        missing_skill = _rpc(client, "resources/read", {"uri": "skill://nope/SKILL.md"})

    assert missing_file["error"]["code"] == -32602
    assert missing_file["error"]["data"]["uri"] == "skill://alpha/missing.md"
    assert missing_skill["error"]["code"] == -32602
    assert missing_skill["error"]["data"]["uri"] == "skill://nope/SKILL.md"


def test_resources_read_detects_modified_skill_file(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        original_skill = _rpc(client, "skills/get", {"uri": "skill://alpha/SKILL.md"})["result"]["skill"]
        original_digest = next(
            entry for entry in original_skill["resources"] if entry["uri"] == "skill://alpha/notes/todo.md"
        )["digest"]

        first = _rpc(client, "resources/read", {"uri": "skill://alpha/notes/todo.md"})["result"]
        assert first["contents"][0]["text"] == "- todo\n"

        (tmp_path / "alpha" / "notes" / "todo.md").write_text("- changed\n")

        second = _rpc(client, "resources/read", {"uri": "skill://alpha/notes/todo.md"})
        assert second["error"]["code"] == -32603
        assert second["error"]["data"]["error"] == "SkillIntegrityError"

        after_skill = _rpc(client, "skills/get", {"uri": "skill://alpha/SKILL.md"})["result"]["skill"]
        after_digest = next(
            entry for entry in after_skill["resources"] if entry["uri"] == "skill://alpha/notes/todo.md"
        )["digest"]
        assert after_digest == original_digest


def test_resources_read_missing_skill_file_is_internal_error(tmp_path: "Path") -> "None":
    with TestClient(app=_make_skills_app(tmp_path)) as client:
        (tmp_path / "alpha" / "notes" / "todo.md").unlink()
        response = _rpc(client, "resources/read", {"uri": "skill://alpha/notes/todo.md"})

    assert response["error"]["code"] == -32603
    assert response["error"]["data"]["error"] == "FileNotFoundError"


def test_resources_read_text_media_type_that_is_not_utf8_falls_back_to_blob(tmp_path: "Path") -> "None":
    _write_skill(tmp_path, "alpha", "Alpha skill.")
    notes_dir = tmp_path / "alpha" / "notes"
    notes_dir.mkdir()
    latin1_bytes = "\xe9\xe8\xea".encode("latin-1")
    (notes_dir / "latin1.txt").write_bytes(latin1_bytes)
    app = Litestar(plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path])))])

    with TestClient(app=app) as client:
        result = _rpc(client, "resources/read", {"uri": "skill://alpha/notes/latin1.txt"})["result"]

    content = result["contents"][0]
    assert "blob" in content
    assert "text" not in content
    assert base64.b64decode(content["blob"]) == latin1_bytes


def test_resources_read_non_utf8_text_over_max_blob_bytes_is_internal_error(tmp_path: "Path") -> "None":
    """A text media type is capped only once the read proves it must fall back to blob."""
    _write_skill(tmp_path, "alpha", "Alpha skill.")
    notes_dir = tmp_path / "alpha" / "notes"
    notes_dir.mkdir()
    (notes_dir / "latin1.txt").write_bytes("\xe9\xe8\xea".encode("latin-1"))
    app = Litestar(plugins=[LitestarMCP(MCPConfig(max_blob_bytes=1, skills=MCPSkillsConfig(paths=[tmp_path])))])

    with TestClient(app=app) as client:
        response = _rpc(client, "resources/read", {"uri": "skill://alpha/notes/latin1.txt"})

    assert response["error"]["code"] == -32603
    assert response["error"]["data"]["error"] == "ValueError"


def test_resources_read_skill_file_wins_over_colliding_handler_uri(tmp_path: "Path") -> "None":
    """A handler cannot shadow a catalog file by declaring the same skill:// mcp_resource_uri.

    The skill-file branch must be checked before the discovered-resource match in
    resources_read; this pins that ordering.
    """

    @get(
        "/shadow",
        mcp_resource="shadow",
        mcp_resource_uri="skill://alpha/SKILL.md",
        mcp_resource_mime_type="text/plain",
        sync_to_thread=False,
    )
    def shadow() -> "str":
        return "HANDLER OUTPUT"

    _write_skill(tmp_path, "alpha", "Alpha skill.", {"notes/todo.md": "- todo\n"})
    _write_skill(tmp_path, "beta", "Beta skill.")
    app = Litestar(
        route_handlers=[shadow],
        plugins=[LitestarMCP(MCPConfig(skills=MCPSkillsConfig(paths=[tmp_path])))],
    )

    with TestClient(app=app) as client:
        result = _rpc(client, "resources/read", {"uri": "skill://alpha/SKILL.md"})["result"]

    content = result["contents"][0]
    assert content["text"] == (tmp_path / "alpha" / "SKILL.md").read_text()
    assert "HANDLER OUTPUT" not in content["text"]
