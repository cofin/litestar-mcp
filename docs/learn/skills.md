# Agent Skills (SEP-2640) & `SkillController`

`litestar-mcp` supports two complementary skill abstractions that bridge MCP instruction bundles, executable tools, and multi-agent runtimes:

1. **Agent Skills over MCP (`io.modelcontextprotocol/skills`, SEP-2640)**: Versioned, SHA-256 digest-locked `SKILL.md` instruction bundles and supporting reference files served through `skills/list`, `skills/get`, `resources/directory/read`, and `resources/read` (`skill://<name>/<relative_path>`). Configured via `MCPConfig(skills=MCPSkillsConfig(...))`.
2. **Class-Based `SkillController`**: A Litestar `Controller` subclass (extending `PromptController`) that unifies `@tool` methods, `@prompt` methods, grounding `instructions`, Litestar `guards`, and `Provide` / `FromDishka[T]` dependency injection into a single reusable unit for `LitestarMCP`, `Agent(skills=[...])`, and A2A `AgentSkill` card projection.

| Concept | Primary Class / Config | Wire Exposure | Typical Use Case |
| :--- | :--- | :--- | :--- |
| **MCP `SKILL.md` Bundles** | `MCPSkillsConfig`, `SkillCatalog`, `Skill`, `SkillFile`, `FilesystemSkillSource`, `SkillSource` | `skills/list`, `skills/get`, `resources/directory/read`, `resources/read` (`skill://...`), plus optional `prompts/list` and `prompts/get` (`expose_as_prompts=True`) | Publishing static or tenant-scoped runbooks, coding standards, and auxiliary scripts that MCP clients and app services read on demand. |
| **Class-Based `SkillController`** | `SkillController` (`@tool`, `@prompt`, `instructions`) | `tools/list`, `tools/call`, `prompts/list`, `prompts/get`, plus `Agent(skills=[...])` | Packaging executable domain tools, prompt templates, and system instructions together with shared controller guards and DI. |
| **A2A `AgentSkill` Metadata** | `SkillController.to_agent_skill()` / `A2AConfig` | `GET /.well-known/agent-card.json` (`skills` array) | Advertising high-level agent capabilities to remote A2A 1.0 peers. |

## Enabling Static Filesystem Skills (MCPSkillsConfig)

Pass an `MCPSkillsConfig` instance with `paths=[...]` (`Sequence[Path]`) to `MCPConfig`:

```python title="docs/examples/snippets/configuration_skills.py"
--8<-- "docs/examples/snippets/configuration_skills.py:example"
```

### MCPSkillsConfig Options

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `paths` | `Sequence[Path]` | `()` | Directories whose immediate child folders containing a `SKILL.md` file are loaded at startup via `FilesystemSkillSource`. Pass `pathlib.Path` instances in a sequence (never a bare `str`). |
| `directory_read` | `bool` | `True` | Whether to advertise `"directoryRead": true` under `capabilities.extensions["io.modelcontextprotocol/skills"]` and serve `resources/directory/read`. |
| `max_files_per_skill` | `int` | `512` | Maximum files (including `SKILL.md`) allowed inside a single filesystem skill folder before startup fails. |
| `max_bytes_per_skill` | `int` | `16_777_216` (`16 MiB`) | Maximum total byte size across all files in a single filesystem skill folder before startup fails. |
| `sources` | `Sequence[SkillSource]` | `()` | Async, request-aware `SkillSource` instances evaluated after `paths`. At least one of `paths` or `sources` must be non-empty. |
| `expose_as_prompts` | `bool` | `False` | When `True`, dynamically projects visible skills into `prompts/list` and `prompts/get` (explicit prompts take precedence on name collisions) and advertises `capabilities.prompts`. |
| `list_changed` | `bool` | `True` | Advertises `"listChanged": true` under `capabilities.extensions["io.modelcontextprotocol/skills"]` and broadcasts `notifications/skills/list_changed` (alongside `notifications/resources/list_changed`). |

## Filesystem Directory Layout & Startup Validation

Each directory in `MCPSkillsConfig.paths` is scanned one level deep for candidate skill directories:

```text
<skills_root>/
  hello-skill/
    SKILL.md
    reference.md
    scripts/
      verify.py
```

1. **Candidate Selection**: Only immediate child directories containing a regular `SKILL.md` file are treated as skills. Child folders without `SKILL.md`, dot-prefixed child folders, and symlinked child folders are skipped.
2. **Recursive File Collection**: Once a skill folder is selected, all regular files inside it are collected recursively (`SKILL.md`, `reference.md`, `scripts/verify.py`), hashed with SHA-256 (`sha256:<64-lowercase-hex>`), and assigned canonical `skill://<name>/<relative_path>` URIs. Dotfiles and symlinked files or subdirectories inside a skill folder are excluded from the manifest.
3. **Startup Validation (`ImproperlyConfiguredException`)**: `FilesystemSkillSource` validates every skill eagerly at startup and raises `ImproperlyConfiguredException` if:
   - A configured entry in `paths` does not exist or is not a directory.
   - `SKILL.md` is a symlink rather than a regular file.
   - `SKILL.md` lacks a valid YAML frontmatter block (`--- ... ---`) or does not parse to a mapping.
   - Frontmatter `name` or `description` is missing, empty, or not a `str`.
   - Frontmatter `name` does not match the skill directory's folder name.
   - Two skill folders across `paths` declare the same `name`.
   - A skill exceeds `max_files_per_skill` or `max_bytes_per_skill`.

## Frontmatter Metadata, Filtering, and Wire Methods

In addition to mandatory `name` and `description` strings, `SKILL.md` YAML frontmatter accepts optional `title`, `icons`, `tags`, and `meta` (`_meta`) fields:

```yaml
---
name: hello-skill
title: Hello Runbook
description: Greets the user and explains how this Litestar server exposes skills.
tags:
  - onboarding
  - ops
icons:
  - src: https://cdn.example.com/icons/runbook.svg
    mimeType: image/svg+xml
meta:
  owner: platform-team
---
```

`Skill` and `SkillFile` propagate `title`, `icons`, `tags`, and `_meta` in `to_entry()` and `to_manifest_entry()`, while `to_resource()` emits `title`, `icons`, and `_meta` per the MCP `Resource` schema and omits `tags`.

When `MCPConfig.skills` is configured, `server/discover` and `initialize` advertise `"io.modelcontextprotocol/skills": {"directoryRead": true, "listChanged": true}` under `capabilities.extensions` (reflecting `directory_read` and `list_changed`), and the router exposes:

- **`skills/list`**: Returns paginated skill manifest entries (`uri`, `frontmatter`, `resources`, plus optional `title`, `icons`, `tags`, and `_meta`). Accepts optional `params.tags` (matching skills sharing any requested tag) and `params.query` (case-insensitive substring search across `name`, `title`, `description`, and `tags`). Global `MCPConfig` `include_tags`, `exclude_tags`, `include_operations`, and `exclude_operations` filters (`filter_skills`) apply automatically across `skills/list`, `skills/get`, `resources/read`, and prompt projections. Each skill entry is atomic—a skill's `resources` array is never split across pages.
- **`skills/get`**: Accepts `{"uri": "skill://<name>/SKILL.md"}` (or `"skill://<name>"`) and returns `{"skill": {...}}`.
- **`resources/directory/read`**: Accepts `{"uri": "skill://<name>"}` or `{"uri": "skill://<name>/<subdir>"}` (without a trailing slash) and lists direct child files and immediate subdirectories (`mimeType: "inode/directory"`). Only registered when `directory_read=True`.
- **`resources/read`**: Reads individual skill files by their `skill://<name>/<relative_path>` URI (and also lists visible skill files in `resources/list`). Every `resources/read` call verifies the returned bytes against `file.size` and `file.digest` (`verify_skill_file`); if a file on disk was modified after startup, `SkillIntegrityError` is raised and mapped to JSON-RPC `INTERNAL_ERROR` (`-32603`).
- **`notifications/skills/list_changed` (`skillsListChanged`)**: When `list_changed=True`, clients subscribing via `POST /mcp` `subscriptions/listen` with `{"notifications": {"skillsListChanged": true}}` (or listening on `2025-*` `GET /mcp` SSE streams) receive `notifications/skills/list_changed` alongside `notifications/resources/list_changed` whenever `plugin.registry.notify_skills_list_changed()` or `await skill_catalog.notify_list_changed()` runs.

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "skills": [
      {
        "uri": "skill://hello-skill/SKILL.md",
        "frontmatter": {
          "name": "hello-skill",
          "description": "Greets the user and explains how this Litestar server exposes skills."
        },
        "resources": [
          {
            "uri": "skill://hello-skill/SKILL.md",
            "name": "SKILL.md",
            "mimeType": "text/markdown",
            "size": 169,
            "digest": "sha256:733c1d9526318ff7dbd07b507eb711ffd4397ea64534cee5c7f6f9a59fcbda12"
          },
          {
            "uri": "skill://hello-skill/reference.md",
            "name": "reference.md",
            "mimeType": "text/markdown",
            "size": 111,
            "digest": "sha256:50d4b6470fcf7748340eae1e76628a3da46c96080ec78bec164b379794558f2d"
          }
        ]
      }
    ]
  }
}
```

## Dynamic, Request-Aware SkillSource Protocol

Applications that load skills from a database, object store, or tenant-scoped registry can implement the `@runtime_checkable` `SkillSource` protocol and pass instances to `MCPSkillsConfig(sources=[...])`:

- **`async def list_skills(self, request: Request[Any, Any, Any], /) -> Sequence[Skill]`**: Return the skills visible to `request`.
- **`async def get_skill(self, request: Request[Any, Any, Any], name: str, /) -> Skill | None`**: Return the named `Skill` if visible to `request`, or `None`.
- **`async def read_file(self, request: Request[Any, Any, Any], skill: Skill, file: SkillFile, /) -> bytes`**: Return the raw bytes for `file` when `file.content` is `None`.

### Constructing In-Memory Skills (Skill.build, Skill.from_content, SkillFile.from_content)

- **`Skill.build(name, description, body="", *, metadata=None, files=None, title=None, icons=None, tags=(), meta=None)`**: Renders YAML frontmatter around `body`, preserves presentation metadata and tags, and computes SHA-256 digests and byte sizes for `SKILL.md` and any auxiliary `files`.
- **`Skill.from_content(skill_md, files=None)`**: Parses raw `SKILL.md` markdown containing frontmatter (`name`, `description`, and optional `title`, `icons`, `tags`, `meta`) and attaches optional auxiliary files.
- **`SkillFile.from_content(skill_name, relative_path, content, *, title=None, icons=None, tags=(), meta=None)`**: Computes `size`, `sha256:<hex>` `digest`, `mime_type`, and canonical `skill://` `uri` from `content`. To serve a large auxiliary file lazily on demand via `source.read_file(...)` while keeping its SHA-256 digest locked in the manifest, use `dataclasses.replace(SkillFile.from_content(...), content=None)`.

```python title="docs/examples/skills_dynamic/main.py"
--8<-- "docs/examples/skills_dynamic/main.py"
```

When both `paths` and `sources` are configured, `SkillCatalog` places `FilesystemSkillSource(paths)` first, followed by `sources` in sequence. On a duplicate skill name across sources, the earlier source in the sequence wins.

!!! important "Cache Scope for Caller-Filtered Skills"
    Keep `MCPConfig.cache_scope="private"` (the default) whenever a `SkillSource` filters skills or file contents by `request.headers`, `request.user`, or tenant context so shared HTTP caches never mix responses across callers.

## Prompt Projection, Request Context, and App-Wide SkillCatalog

Setting `MCPSkillsConfig(expose_as_prompts=True)` dynamically advertises `capabilities.prompts` and projects every visible skill into `prompts/list` and `prompts/get` as a zero-argument prompt returning its digest-verified `SKILL.md` content. Explicit `@mcp_prompt`, route-bound `mcp_prompt`, and `PromptController` registrations always take precedence on a name collision—see [Prompts & `PromptController`](prompts.md).

### Optional MCP Request Context Lookup

Shared service helpers and `SkillSource` implementations called from both MCP operations and regular HTTP routes or CLI commands can inspect the active MCP context via the public `get_mcp_request_context` overloads:

```python
from typing import TypeVar, overload

from litestar_mcp.mcp.service import MCPRequestContext

T = TypeVar("T")


@overload
def get_mcp_request_context() -> MCPRequestContext: ...


@overload
def get_mcp_request_context(default: T) -> MCPRequestContext | T: ...
```

Calling `get_mcp_request_context()` without arguments raises `RuntimeError` outside an active MCP dispatch, while passing a fallback such as `get_mcp_request_context(None)` (or `get_mcp_request_context(default=None)`) returns `None` outside MCP dispatch and the active `MCPRequestContext` inside one.

### Request-Optional SkillCatalog and Dependency Injection

`SkillCatalog` is exported directly from `litestar_mcp` (`from litestar_mcp import SkillCatalog`), bound to the `Litestar` app via `bind_app`, and registered automatically as an app-wide Litestar dependency named `skill_catalog` (`app_config.dependencies.setdefault("skill_catalog", ...)`). Every catalog method—`list_skills`, `get_skill`, `get`, `get_file`, `list_directory`, `resource_entries`, and `read_file`—accepts `request=None` (or omits `request`), synthesizing a lightweight blank internal `Request` when called from startup hooks, background jobs, or CLI tasks:

```python
from litestar import get

from litestar_mcp import SkillCatalog, get_mcp_request_context


@get("/api/runbooks")
async def list_runbooks(skill_catalog: SkillCatalog) -> dict[str, object]:
    """Query the shared SkillCatalog from a standard HTTP route."""
    mcp_ctx = get_mcp_request_context(None)
    skills = await skill_catalog.list_skills(tags=["ops"], query="runbook")
    return {
        "session_id": mcp_ctx.session_id if mcp_ctx is not None else None,
        "skills": [skill.name for skill in skills],
    }
```

### Runnable Prompt Projection and Catalog Injection

The runnable snippet below configures `MCPSkillsConfig(expose_as_prompts=True, list_changed=True)` and injects `SkillCatalog` into a standard HTTP route:

```python title="docs/examples/snippets/skills_prompts_and_catalog.py"
--8<-- "docs/examples/snippets/skills_prompts_and_catalog.py:example"
```

## Client Helpers and CLI Skill Discovery

Remote consumers and local operators can inspect SEP-2640 catalogs directly through `MCPStreamableHTTPClient` and the `litestar mcp` CLI:

- **`MCPStreamableHTTPClient` Skill Methods**: Call `await client.list_skills(tags=["ops"], query="runbook")` to fetch filtered skill manifests, `await client.get_skill("skill://hello-skill/SKILL.md")` to retrieve a single skill entry, and `await client.read_skill_directory("skill://hello-skill")` to enumerate directory entries over `resources/directory/read`.
- **`litestar mcp list-skills` CLI**: Run `litestar --app app:app mcp list-skills` (with optional `--tag <tag>` and `--query <text>` flags) to print the application's configured skills directly from the app-bound `SkillCatalog`.

## Class-Based SkillController

While `MCPSkillsConfig` serves `SKILL.md` document bundles over `resources/*`, `SkillController` lets you package executable `@tool` methods, `@prompt` methods, system `instructions`, Litestar `guards`, and `dependencies` into a single Python class:

```python title="docs/examples/agent/coffee_shop_agent.py"
--8<-- "docs/examples/agent/coffee_shop_agent.py:24:68"
```

A `SkillController` can be mounted in three places simultaneously:

1. **On `Litestar(route_handlers=[BaristaSkillController])` or `MCPConfig(skill_controllers=[BaristaSkillController])`**: Registers every `@tool` method in `tools/list` / `tools/call` and every `@prompt` method in `prompts/list` / `prompts/get` (namespaced under `prefix` or `name`), protected by the controller's `guards` and `dependencies`.
2. **On `Agent(skills=[BaristaSkillController])`**: Injects the controller's `instructions` into the agent's system prompt and binds all `@tool` methods to the agent's toolset with full DI support.
3. **On `LitestarA2A` (`to_agent_skill()`)**: Projects `id`, `name`, `description`, `tags`, and `examples` into the A2A `AgentCard` served at `/.well-known/agent-card.json`.
