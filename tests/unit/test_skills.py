"""Skills over MCP extension tests: advertisement and wire methods."""

from pathlib import Path  # noqa: TC003
from typing import Any, cast

from litestar import Litestar
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


def _make_skills_app(root: "Path", *, tasks: "bool" = False, **config_kwargs: "Any") -> "Litestar":
    _write_skill(root, "alpha", "Alpha skill.", {"notes/todo.md": "- todo\n"})
    _write_skill(root, "beta", "Beta skill.")
    return Litestar(
        plugins=[LitestarMCP(MCPConfig(tasks=tasks, skills=MCPSkillsConfig(paths=[root], **config_kwargs)))]
    )


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
    alpha = result["skills"][0]
    assert set(alpha) == {"uri", "frontmatter", "resources"}
    assert len(alpha["resources"]) == 2
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
    assert "size" in skill_md
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
