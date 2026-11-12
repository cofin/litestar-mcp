"""Filesystem and dynamic skill sources and catalog for the Skills over MCP extension."""

import copy
import hashlib
import logging
import mimetypes
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, overload, runtime_checkable

import anyio
import yaml
from litestar import Request
from litestar.exceptions import ImproperlyConfiguredException

if TYPE_CHECKING:
    from litestar import Litestar

    from litestar_mcp.mcp.config import MCPSkillsConfig
    from litestar_mcp.mcp.registry import Registry

__all__ = (
    "SKILL_URI_PREFIX",
    "FilesystemSkillSource",
    "Skill",
    "SkillCatalog",
    "SkillFile",
    "SkillFileMatch",
    "SkillIntegrityError",
    "SkillSource",
    "compute_digest",
    "filter_skills",
    "guess_skill_mime_type",
    "load_skill",
    "parse_frontmatter",
    "parse_skill_uri",
    "verify_skill_file",
)

SKILL_URI_PREFIX = "skill://"
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
_SHA256_DIGEST_PREFIX = "sha256:"
_SHA256_DIGEST_LENGTH = 71
_DIGEST_HEX_CHARS = frozenset("0123456789abcdef")
_UNSET = object()
_MAX_POSITIONAL_ARGS = 2
_logger = logging.getLogger(__name__)


class SkillIntegrityError(Exception):
    """Raised when a skill file's on-disk or source-returned bytes no longer match its manifest digest."""


def compute_digest(data: "bytes") -> "str":
    """Return the ``sha256:<hex>`` digest of ``data``."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def guess_skill_mime_type(relative_path: "str") -> "str":
    """Guess a MIME type for ``relative_path``, with a markdown override and binary fallback."""
    suffix = Path(relative_path).suffix.lower()
    if suffix in _MARKDOWN_EXTENSIONS:
        return "text/markdown"
    guessed, _ = mimetypes.guess_type(relative_path)
    return guessed or "application/octet-stream"


@dataclass(frozen=True, slots=True)
class SkillFile:
    """One file inside a skill folder or dynamic skill manifest."""

    relative_path: "str"
    """Forward-slash path relative to the skill root."""
    path: "Path | None"
    """Absolute filesystem path on disk, or `None` for in-memory/lazy files."""
    uri: "str"
    """Canonical `skill://<name>/<relative_path>` URI."""
    mime_type: "str"
    """Resolved MIME type (`application/octet-stream` fallback)."""
    size: "int"
    """Byte length recorded in the skill manifest."""
    digest: "str"
    """`sha256:<hex>` digest recorded in the skill manifest."""
    content: "bytes | None" = field(default=None, repr=False)
    """Optional inline byte payload served without calling `source.read_file`."""
    title: "str | None" = None
    """Optional human-readable display title for the file."""
    icons: "list[dict[str, Any]] | None" = field(default=None, hash=False)
    """Optional MCP icon descriptors attached to this file."""
    tags: "tuple[str, ...]" = ()
    """Optional skill-file tags used for internal filtering."""
    meta: "dict[str, Any] | None" = field(default=None, hash=False)
    """Optional ``_meta`` payload attached to this file."""

    def __post_init__(self) -> "None":
        """Validate path, URI, size, digest, inline content, and metadata invariants."""
        _validate_relative_path(self.relative_path)
        parsed = parse_skill_uri(self.uri)
        if parsed is None or parsed[1] != self.relative_path:
            msg = f"SkillFile uri {self.uri!r} does not match relative_path {self.relative_path!r}"
            raise ValueError(msg)
        if not isinstance(self.mime_type, str) or not self.mime_type:
            msg = "SkillFile mime_type must be a non-empty string"
            raise ValueError(msg)
        if not isinstance(self.size, int) or self.size < 0:
            msg = f"SkillFile size must be a non-negative integer, got {self.size!r}"
            raise ValueError(msg)
        if not _is_valid_sha256_digest(self.digest):
            msg = f"SkillFile digest must be 'sha256:<64 lowercase hex chars>', got {self.digest!r}"
            raise ValueError(msg)
        if self.content is not None:
            if len(self.content) != self.size:
                msg = f"SkillFile inline content size ({len(self.content)}) does not match manifest size ({self.size})"
                raise ValueError(msg)
            actual_digest = compute_digest(self.content)
            if actual_digest != self.digest:
                msg = (
                    f"SkillFile inline content digest ({actual_digest}) does not match manifest digest ({self.digest})"
                )
                raise ValueError(msg)
        object.__setattr__(self, "title", _normalize_title(self.title))
        object.__setattr__(self, "icons", _copy_icons(self.icons))
        object.__setattr__(self, "tags", _normalize_tags(self.tags))
        object.__setattr__(self, "meta", _copy_meta(self.meta))

    @classmethod
    def from_content(
        cls,
        skill_name: "str",
        relative_path: "str",
        content: "str | bytes",
        *,
        title: "str | None" = None,
        icons: "Sequence[Mapping[str, Any]] | None" = None,
        tags: "Sequence[str]" = (),
        meta: "Mapping[str, Any] | None" = None,
    ) -> "SkillFile":
        """Construct an in-memory ``SkillFile`` with manifest metadata computed from ``content``."""
        _validate_skill_name(skill_name)
        _validate_relative_path(relative_path)
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return cls(
            relative_path=relative_path,
            path=None,
            uri=f"{SKILL_URI_PREFIX}{skill_name}/{relative_path}",
            mime_type=guess_skill_mime_type(relative_path),
            size=len(raw),
            digest=compute_digest(raw),
            content=raw,
            title=title,
            icons=_copy_icons(icons),
            tags=_normalize_tags(tags),
            meta=_copy_meta(meta),
        )

    def to_manifest_entry(self) -> "dict[str, Any]":
        """Return the ``skills/list`` / ``skills/get`` resource manifest entry."""
        entry: dict[str, Any] = {
            "uri": self.uri,
            "name": self.relative_path,
            "mimeType": self.mime_type,
            "size": self.size,
            "digest": self.digest,
        }
        if self.title is not None:
            entry["title"] = self.title
        if self.icons:
            entry["icons"] = [copy.deepcopy(dict(icon)) for icon in self.icons]
        if self.tags:
            entry["tags"] = list(self.tags)
        if self.meta:
            entry["_meta"] = copy.deepcopy(dict(self.meta))
        return entry

    def to_resource(self, skill: "Skill") -> "dict[str, Any]":
        """Return the ``resources/list`` entry for this file within ``skill``."""
        entry: dict[str, Any] = {
            "uri": self.uri,
            "name": self.relative_path,
            "mimeType": self.mime_type,
            "size": self.size,
        }
        is_skill_md = self.relative_path == "SKILL.md"
        if is_skill_md:
            entry["description"] = skill.description
        title = self.title if self.title is not None else (skill.title if is_skill_md else None)
        if title is not None:
            entry["title"] = title
        icons = self.icons if self.icons is not None else (skill.icons if is_skill_md else None)
        if icons:
            entry["icons"] = [copy.deepcopy(dict(icon)) for icon in icons]
        meta = self.meta if self.meta is not None else (skill.meta if is_skill_md else None)
        if meta:
            entry["_meta"] = copy.deepcopy(dict(meta))
        return entry


def verify_skill_file(file: "SkillFile", data: "bytes") -> "bytes":
    """Verify ``data`` matches ``file.size`` and ``file.digest``, or raise ``SkillIntegrityError``."""
    if len(data) != file.size or compute_digest(data) != file.digest:
        msg = f"Skill file failed integrity verification: {file.uri}"
        raise SkillIntegrityError(msg)
    return data


@dataclass(frozen=True, slots=True)
class Skill:
    """One skill loaded from a skill directory or constructed dynamically."""

    name: "str"
    """Skill identifier, matching the `name` frontmatter key."""
    description: "str"
    """Human-readable summary from frontmatter."""
    uri: "str"
    """Canonical `skill://<name>/SKILL.md` URI."""
    frontmatter: "dict[str, Any]"
    """Full parsed YAML frontmatter mapping, preserved verbatim."""
    files: "tuple[SkillFile, ...]"
    """Every file belonging to the skill, sorted by `relative_path`."""
    title: "str | None" = None
    """Optional human-readable display title."""
    icons: "list[dict[str, Any]] | None" = field(default=None, hash=False)
    """Optional MCP icon descriptors."""
    tags: "tuple[str, ...]" = ()
    """Optional tags for capability and client filtering."""
    meta: "dict[str, Any] | None" = field(default=None, hash=False)
    """Optional ``_meta`` metadata payload."""

    def __post_init__(self) -> "None":
        """Validate skill identity, frontmatter consistency, file URIs, metadata, and sort files."""
        _validate_skill_name(self.name)
        if not isinstance(self.description, str) or not self.description.strip():
            msg = "Skill description must be a non-empty string"
            raise ValueError(msg)
        expected_uri = f"{SKILL_URI_PREFIX}{self.name}/SKILL.md"
        if self.uri != expected_uri:
            msg = f"Skill uri must be {expected_uri!r}, got {self.uri!r}"
            raise ValueError(msg)
        if self.frontmatter.get("name") != self.name:
            msg = f"Skill frontmatter 'name' ({self.frontmatter.get('name')!r}) must match name ({self.name!r})"
            raise ValueError(msg)
        if self.frontmatter.get("description") != self.description:
            msg = (
                f"Skill frontmatter 'description' ({self.frontmatter.get('description')!r}) "
                f"must match description ({self.description!r})"
            )
            raise ValueError(msg)

        seen_paths: set[str] = set()
        prefix = f"{SKILL_URI_PREFIX}{self.name}/"
        for file in self.files:
            expected_file_uri = f"{prefix}{file.relative_path}"
            if file.uri != expected_file_uri:
                msg = f"SkillFile uri {file.uri!r} does not belong to skill {self.name!r}"
                raise ValueError(msg)
            if file.relative_path in seen_paths:
                msg = f"Skill {self.name!r} contains duplicate file path {file.relative_path!r}"
                raise ValueError(msg)
            seen_paths.add(file.relative_path)

        if "SKILL.md" not in seen_paths:
            msg = f"Skill {self.name!r} must include a 'SKILL.md' file"
            raise ValueError(msg)

        copied_frontmatter = copy.deepcopy(dict(self.frontmatter))
        object.__setattr__(self, "frontmatter", copied_frontmatter)
        sorted_files = tuple(sorted(self.files, key=lambda item: item.relative_path))
        object.__setattr__(self, "files", sorted_files)

        normalized_title = (
            _normalize_title(self.title)
            if self.title is not None
            else _normalize_title(copied_frontmatter.get("title"))
        )
        normalized_icons = (
            _copy_icons(self.icons) if self.icons is not None else _copy_icons(copied_frontmatter.get("icons"))
        )
        normalized_tags = _normalize_tags(self.tags)
        if not normalized_tags:
            normalized_tags = _extract_frontmatter_tags(copied_frontmatter.get("tags"))
        normalized_meta = (
            _copy_meta(self.meta) if self.meta is not None else _copy_meta(copied_frontmatter.get("_meta"))
        )

        object.__setattr__(self, "title", normalized_title)
        object.__setattr__(self, "icons", normalized_icons)
        object.__setattr__(self, "tags", normalized_tags)
        object.__setattr__(self, "meta", normalized_meta)

    @classmethod
    def from_content(
        cls,
        skill_md: "str | bytes",
        files: "Mapping[str, str | bytes | SkillFile] | None" = None,
    ) -> "Skill":
        """Construct an in-memory ``Skill`` from ``SKILL.md`` content and optional auxiliary files."""
        if isinstance(skill_md, bytes):
            try:
                text = skill_md.decode("utf-8")
            except UnicodeDecodeError as exc:
                msg = f"SKILL.md is not valid UTF-8: {exc}"
                raise ValueError(msg) from exc
        else:
            text = skill_md

        try:
            frontmatter = parse_frontmatter(text)
        except ImproperlyConfiguredException as exc:
            raise ValueError(str(exc)) from exc

        name = frontmatter.get("name")
        if not isinstance(name, str) or not name.strip():
            msg = "SKILL.md frontmatter must declare a non-empty string 'name'"
            raise ValueError(msg)
        _validate_skill_name(name)

        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            msg = f"Skill '{name}' frontmatter must declare a non-empty string 'description'"
            raise ValueError(msg)

        skill_md_file = SkillFile.from_content(name, "SKILL.md", skill_md)
        collected: list[SkillFile] = [skill_md_file]
        for rel_path, item in (files or {}).items():
            if rel_path == "SKILL.md":
                msg = "files mapping must not override 'SKILL.md'"
                raise ValueError(msg)
            if isinstance(item, SkillFile):
                if item.relative_path != rel_path:
                    msg = f"SkillFile relative_path {item.relative_path!r} does not match mapping key {rel_path!r}"
                    raise ValueError(msg)
                collected.append(item)
            else:
                collected.append(SkillFile.from_content(name, rel_path, item))

        return cls(
            name=name,
            description=description,
            uri=skill_md_file.uri,
            frontmatter=dict(frontmatter),
            files=tuple(collected),
        )

    @classmethod
    def build(
        cls,
        name: "str",
        description: "str",
        body: "str" = "",
        *,
        metadata: "Mapping[str, Any] | None" = None,
        files: "Mapping[str, str | bytes | SkillFile] | None" = None,
        title: "str | None" = None,
        icons: "Sequence[Mapping[str, Any]] | None" = None,
        tags: "Sequence[str] | None" = None,
        meta: "Mapping[str, Any] | None" = None,
    ) -> "Skill":
        """Build an in-memory ``Skill`` by rendering YAML-safe frontmatter around ``body``."""
        _validate_skill_name(name)
        if not isinstance(description, str) or not description.strip():
            msg = "Skill description must be a non-empty string"
            raise ValueError(msg)
        if metadata is not None:
            for reserved in ("name", "description"):
                if reserved in metadata:
                    msg = f"Skill.build metadata must not contain reserved key {reserved!r}"
                    raise ValueError(msg)

        fm_mapping: dict[str, Any] = {"name": name, "description": description}
        if metadata:
            fm_mapping.update(copy.deepcopy(dict(metadata)))
        if title is not None:
            fm_mapping.pop("title", None)
            normalized_title = _normalize_title(title)
            if normalized_title is not None:
                fm_mapping["title"] = normalized_title
        if icons is not None:
            fm_mapping.pop("icons", None)
            normalized_icons = _copy_icons(icons)
            if normalized_icons is not None:
                fm_mapping["icons"] = normalized_icons
        if tags is not None:
            fm_mapping.pop("tags", None)
            normalized_tags = _normalize_tags(tags)
            if normalized_tags:
                fm_mapping["tags"] = list(normalized_tags)
        if meta is not None:
            fm_mapping.pop("_meta", None)
            normalized_meta = _copy_meta(meta)
            if normalized_meta is not None:
                fm_mapping["_meta"] = normalized_meta
        rendered_fm = yaml.safe_dump(fm_mapping, sort_keys=False, allow_unicode=True).rstrip("\n")
        skill_md_text = f"---\n{rendered_fm}\n---\n\n{body}" if body else f"---\n{rendered_fm}\n---\n"
        return cls.from_content(skill_md_text, files=files)

    def get_file(self, relative_path: "str") -> "SkillFile | None":
        """Return the ``SkillFile`` at ``relative_path`` or ``None`` if absent."""
        for file in self.files:
            if file.relative_path == relative_path:
                return file
        return None

    def list_directory(self, relative_path: "str" = "") -> "list[dict[str, Any]] | None":
        """List files and immediate subdirectories under ``relative_path`` (``""`` for skill root)."""
        if relative_path and any(file.relative_path == relative_path for file in self.files):
            return None

        prefix = f"{relative_path}/" if relative_path else ""
        matching_files = (
            [file for file in self.files if file.relative_path.startswith(prefix)] if prefix else list(self.files)
        )
        if not matching_files:
            return None

        direct_files: list[dict[str, Any]] = []
        subdirectories: set[str] = set()
        for file in matching_files:
            remainder = file.relative_path[len(prefix) :]
            if "/" not in remainder:
                direct_files.append(file.to_resource(self))
            else:
                first_segment = remainder.split("/", 1)[0]
                subdirectories.add(first_segment)

        directory_entries: list[dict[str, Any]] = [
            {
                "uri": f"{SKILL_URI_PREFIX}{self.name}/{prefix}{subdir}",
                "name": subdir,
                "mimeType": "inode/directory",
            }
            for subdir in sorted(subdirectories)
        ]

        combined = direct_files + directory_entries
        combined.sort(key=lambda entry: entry["name"])
        return combined

    def to_entry(self) -> "dict[str, Any]":
        """Return the ``skills/list`` / ``skills/get`` entry for this skill."""
        entry: dict[str, Any] = {
            "uri": self.uri,
            "frontmatter": copy.deepcopy(self.frontmatter),
            "resources": [file.to_manifest_entry() for file in self.files],
        }
        if self.title is not None:
            entry["title"] = self.title
        if self.icons:
            entry["icons"] = [copy.deepcopy(dict(icon)) for icon in self.icons]
        if self.tags:
            entry["tags"] = list(self.tags)
        if self.meta:
            entry["_meta"] = copy.deepcopy(dict(self.meta))
        return entry

    def to_resources(self) -> "list[dict[str, Any]]":
        """Return the ``resources/list`` entries for every file in this skill."""
        return [file.to_resource(self) for file in self.files]


def filter_skills(
    skills: "Sequence[Skill]",
    *,
    tags: "Sequence[str] | None" = None,
    query: "str | None" = None,
) -> "list[Skill]":
    """Filter ``skills`` by tag intersection and case-insensitive substring ``query``."""
    tag_filter = {tag for tag in tags if isinstance(tag, str) and tag} if tags else set()
    query_text = query.strip().casefold() if isinstance(query, str) else ""
    matched: list[Skill] = []
    for skill in skills:
        if tag_filter and not (tag_filter & set(skill.tags)):
            continue
        if query_text:
            in_name = query_text in skill.name.casefold()
            in_description = query_text in skill.description.casefold()
            in_title = query_text in (skill.title or "").casefold()
            in_tags = any(query_text in tag.casefold() for tag in skill.tags)
            if not (in_name or in_description or in_title or in_tags):
                continue
        matched.append(skill)
    return matched


@runtime_checkable
class SkillSource(Protocol):
    """Async, request-aware source of Agent Skills."""

    async def list_skills(self, request: "Request[Any, Any, Any]", /) -> "Sequence[Skill]":
        """Every skill the caller of ``request`` may see; names unique within the result."""
        ...

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
        """The skill named ``name`` iff ``list_skills(request)`` would include it."""
        ...

    async def read_file(self, request: "Request[Any, Any, Any]", skill: "Skill", file: "SkillFile", /) -> "bytes":
        """Bytes of ``file`` (of ``skill`` as returned by this source in the same request)."""
        ...


@dataclass(frozen=True, slots=True)
class SkillFileMatch:
    """Resolved skill file paired with its owning ``SkillSource`` and ``Skill``."""

    source: "SkillSource"
    """The `SkillSource` that produced the matched skill."""
    skill: "Skill"
    """The `Skill` owning the matched file."""
    file: "SkillFile"
    """The matched `SkillFile` manifest entry."""


class FilesystemSkillSource:
    """Eager, digest-locked filesystem source of Agent Skills."""

    __slots__ = ("_paths", "_skills", "_skills_by_name")

    def __init__(
        self,
        paths: "Sequence[Path]",
        *,
        max_files_per_skill: "int" = 512,
        max_bytes_per_skill: "int" = 16_777_216,
    ) -> "None":
        if max_files_per_skill <= 0:
            msg = "max_files_per_skill must be positive"
            raise ValueError(msg)
        if max_bytes_per_skill <= 0:
            msg = "max_bytes_per_skill must be positive"
            raise ValueError(msg)

        normalized_paths = tuple(Path(p) for p in paths)
        skills_by_name: dict[str, tuple[Skill, Path]] = {}
        for root in normalized_paths:
            if not root.exists() or not root.is_dir():
                msg = f"Configured skill path is not an existing directory: {root}"
                raise ImproperlyConfiguredException(msg)

            children = sorted(root.iterdir(), key=lambda entry: entry.name)
            for child in children:
                if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                    continue
                skill_md = child / "SKILL.md"
                if skill_md.is_symlink():
                    msg = f"Skill '{child.name}' has a symlinked SKILL.md ({skill_md}), which is not allowed"
                    raise ImproperlyConfiguredException(msg)
                if not skill_md.is_file():
                    continue

                skill = load_skill(
                    child,
                    max_files=max_files_per_skill,
                    max_bytes=max_bytes_per_skill,
                )
                if skill.name in skills_by_name:
                    _, first_dir = skills_by_name[skill.name]
                    msg = f"Duplicate skill name '{skill.name}' found in {first_dir} and {child}"
                    raise ImproperlyConfiguredException(msg)
                skills_by_name[skill.name] = (skill, child)

        sorted_skills = tuple(sorted((entry[0] for entry in skills_by_name.values()), key=lambda item: item.name))
        self._paths = normalized_paths
        self._skills = sorted_skills
        self._skills_by_name: dict[str, Skill] = {skill.name: skill for skill in sorted_skills}

    @property
    def paths(self) -> "tuple[Path, ...]":
        """Configured root directories searched by this source."""
        return self._paths

    @property
    def skills(self) -> "tuple[Skill, ...]":
        """Loaded skills sorted by ``name``."""
        return self._skills

    async def list_skills(self, request: "Request[Any, Any, Any]", /) -> "tuple[Skill, ...]":
        """Return all filesystem skills sorted by ``name``."""
        del request
        return self._skills

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
        """Return the filesystem skill named ``name``, or ``None``."""
        del request
        return self._skills_by_name.get(name)

    async def read_file(self, request: "Request[Any, Any, Any]", skill: "Skill", file: "SkillFile", /) -> "bytes":
        """Read ``file.path`` from disk asynchronously."""
        del request, skill
        if file.path is None:
            msg = f"Skill file has no filesystem path: {file.uri}"
            raise LookupError(msg)
        return await anyio.Path(file.path).read_bytes()


class SkillCatalog:
    """Request-aware composition of `SkillSource` instances."""

    __slots__ = ("_app_ref", "_registry", "_sources", "directory_read", "expose_as_prompts", "list_changed")

    directory_read: "bool"
    """Whether `resources/directory/read` is enabled for skill directories."""
    list_changed: "bool"
    """Whether `listChanged` is advertised on the skills capability extension."""
    expose_as_prompts: "bool"
    """Whether skills are projected as read-only MCP prompts when no `@mcp.prompt` collides."""

    def __init__(
        self,
        sources: "Sequence[SkillSource]",
        *,
        directory_read: "bool" = True,
        list_changed: "bool" = True,
        expose_as_prompts: "bool" = False,
    ) -> "None":
        self._sources: tuple[SkillSource, ...] = tuple(sources)
        self._app_ref: Litestar | None = None
        self._registry: Registry | None = None
        self.directory_read = directory_read
        self.list_changed = list_changed
        self.expose_as_prompts = expose_as_prompts

    @property
    def sources(self) -> "tuple[SkillSource, ...]":
        """Tuple of composed `SkillSource` instances in priority order."""
        return self._sources

    @classmethod
    def from_config(cls, config: "MCPSkillsConfig") -> "SkillCatalog":
        """Build a catalog from ``config`` (`paths` first as `FilesystemSkillSource`, then ``sources``)."""
        composed: list[SkillSource] = []
        if config.paths:
            composed.append(
                FilesystemSkillSource(
                    config.paths,
                    max_files_per_skill=config.max_files_per_skill,
                    max_bytes_per_skill=config.max_bytes_per_skill,
                )
            )
        composed.extend(config.sources)
        return cls(
            sources=composed,
            directory_read=config.directory_read,
            list_changed=config.list_changed,
            expose_as_prompts=config.expose_as_prompts,
        )

    def bind_app(self, app: "Litestar | None", *, registry: "Registry | None" = None) -> "None":
        """Bind a Litestar ``app`` and optional ``registry`` for request synthesis and change notifications."""
        if app is not None:
            self._app_ref = app
        if registry is not None:
            self._registry = registry
            registry.configure_skill_notifications(
                list_changed=self.list_changed,
                expose_as_prompts=self.expose_as_prompts,
            )

    def preloaded_skills(self) -> "tuple[Skill, ...]":
        """Return eagerly loaded static skills from filesystem sources in priority order."""
        by_name: dict[str, Skill] = {}
        for source in self._sources:
            if isinstance(source, FilesystemSkillSource):
                for skill in source.skills:
                    by_name.setdefault(skill.name, skill)
        return tuple(by_name[name] for name in sorted(by_name))

    async def notify_list_changed(self) -> "None":
        """Emit skill list-changed (and prompt list-changed when ``expose_as_prompts`` is enabled) notifications."""
        if self._registry is not None:
            self._registry.configure_skill_notifications(
                list_changed=self.list_changed,
                expose_as_prompts=self.expose_as_prompts,
            )
            await self._registry.notify_skills_list_changed()

    async def list_skills(
        self,
        request: "Request[Any, Any, Any] | None" = None,
        *,
        tags: "Sequence[str] | None" = None,
        query: "str | None" = None,
    ) -> "list[Skill]":
        """Return every skill visible to ``request`` across all sources, optionally filtered and sorted by name."""
        resolved_request = self._resolve_request(request)
        by_name: dict[str, Skill] = {}
        for source in self._sources:
            source_skills = await source.list_skills(resolved_request)
            seen_in_source: set[str] = set()
            for skill in source_skills:
                if skill.name in seen_in_source:
                    msg = f"SkillSource {source!r} returned duplicate skill name {skill.name!r}"
                    raise ImproperlyConfiguredException(msg)
                seen_in_source.add(skill.name)
                if skill.name in by_name:
                    _logger.warning(
                        "Skill %r from source %r is shadowed by an earlier source; keeping first definition.",
                        skill.name,
                        source,
                    )
                    continue
                by_name[skill.name] = skill
        sorted_skills = sorted(by_name.values(), key=lambda item: item.name)
        if tags is not None or query is not None:
            return filter_skills(sorted_skills, tags=tags, query=query)
        return sorted_skills

    @overload
    async def get_skill(
        self,
        request: "Request[Any, Any, Any] | None",
        name: "str",
    ) -> "Skill | None": ...

    @overload
    async def get_skill(
        self,
        name: "str",
        *,
        request: "Request[Any, Any, Any] | None" = None,
    ) -> "Skill | None": ...

    async def get_skill(self, *args: "Any", **kwargs: "Any") -> "Skill | None":
        """Return the skill named ``name`` visible to ``request``, or ``None``."""
        req, name = self._resolve_request_and_target(
            args,
            kwargs,
            target_name="name",
            target_type=str,
            method_name="get_skill",
        )
        resolved = await self._resolve_skill_with_source(req, name)
        return resolved[1] if resolved is not None else None

    @overload
    async def get(
        self,
        request: "Request[Any, Any, Any] | None",
        uri: "str",
    ) -> "Skill | None": ...

    @overload
    async def get(
        self,
        uri: "str",
        *,
        request: "Request[Any, Any, Any] | None" = None,
    ) -> "Skill | None": ...

    async def get(self, *args: "Any", **kwargs: "Any") -> "Skill | None":
        """Return the skill whose canonical ``skill://<name>/SKILL.md`` or shorthand ``skill://<name>`` URI matches."""
        req, uri = self._resolve_request_and_target(
            args,
            kwargs,
            target_name="uri",
            target_type=str,
            method_name="get",
        )
        parsed = parse_skill_uri(uri)
        if parsed is None:
            return None
        name, relative_path = parsed
        if relative_path not in {"", "SKILL.md"}:
            return None
        return await self.get_skill(req, name)

    @overload
    async def get_file(
        self,
        request: "Request[Any, Any, Any] | None",
        uri: "str",
    ) -> "SkillFileMatch | None": ...

    @overload
    async def get_file(
        self,
        uri: "str",
        *,
        request: "Request[Any, Any, Any] | None" = None,
    ) -> "SkillFileMatch | None": ...

    async def get_file(self, *args: "Any", **kwargs: "Any") -> "SkillFileMatch | None":
        """Resolve ``uri`` to a `SkillFileMatch` visible to ``request``, or ``None``."""
        req, uri = self._resolve_request_and_target(
            args,
            kwargs,
            target_name="uri",
            target_type=str,
            method_name="get_file",
        )
        parsed = parse_skill_uri(uri)
        if parsed is None:
            return None
        name, relative_path = parsed
        if not relative_path:
            return None
        resolved = await self._resolve_skill_with_source(req, name)
        if resolved is None:
            return None
        source, skill = resolved
        skill_file = skill.get_file(relative_path)
        if skill_file is None:
            return None
        return SkillFileMatch(source=source, skill=skill, file=skill_file)

    @overload
    async def list_directory(
        self,
        request: "Request[Any, Any, Any] | None",
        uri: "str",
    ) -> "list[dict[str, Any]] | None": ...

    @overload
    async def list_directory(
        self,
        uri: "str",
        *,
        request: "Request[Any, Any, Any] | None" = None,
    ) -> "list[dict[str, Any]] | None": ...

    async def list_directory(self, *args: "Any", **kwargs: "Any") -> "list[dict[str, Any]] | None":
        """List direct file and directory children under ``uri``, or ``None`` if not a visible directory."""
        req, uri = self._resolve_request_and_target(
            args,
            kwargs,
            target_name="uri",
            target_type=str,
            method_name="list_directory",
        )
        parsed = parse_skill_uri(uri)
        if parsed is None:
            return None
        name, relative_path = parsed
        skill = await self.get_skill(req, name)
        if skill is None:
            return None
        return skill.list_directory(relative_path)

    async def resource_entries(
        self,
        request: "Request[Any, Any, Any] | None" = None,
    ) -> "list[dict[str, Any]]":
        """Return ``resources/list`` entries for every file in every visible skill."""
        resolved_request = self._resolve_request(request)
        entries: list[dict[str, Any]] = []
        for skill in await self.list_skills(resolved_request):
            entries.extend(skill.to_resources())
        return entries

    @overload
    async def read_file(
        self,
        request: "Request[Any, Any, Any] | None",
        match: "SkillFileMatch",
    ) -> "bytes": ...

    @overload
    async def read_file(
        self,
        match: "SkillFileMatch",
        *,
        request: "Request[Any, Any, Any] | None" = None,
    ) -> "bytes": ...

    async def read_file(self, *args: "Any", **kwargs: "Any") -> "bytes":
        """Read ``match.file`` (inline or via ``match.source.read_file``) and verify size and digest."""
        req, match = self._resolve_request_and_target(
            args,
            kwargs,
            target_name="match",
            target_type=SkillFileMatch,
            method_name="read_file",
        )
        if match.file.content is not None:
            data = match.file.content
        else:
            data = await match.source.read_file(req, match.skill, match.file)
        return await anyio.to_thread.run_sync(verify_skill_file, match.file, data)

    async def _resolve_skill_with_source(
        self,
        request: "Request[Any, Any, Any]",
        name: "str",
    ) -> "tuple[SkillSource, Skill] | None":
        for source in self._sources:
            skill = await source.get_skill(request, name)
            if skill is None:
                continue
            if skill.name != name:
                msg = f"SkillSource {source!r} returned skill {skill.name!r} for requested name {name!r}"
                raise ImproperlyConfiguredException(msg)
            return source, skill
        return None

    def _resolve_request(self, request: "Request[Any, Any, Any] | None") -> "Request[Any, Any, Any]":
        if request is not None:
            return request
        return _blank_skill_request(self._app_ref)

    def _resolve_request_and_target(
        self,
        args: "tuple[Any, ...]",
        kwargs: "dict[str, Any]",
        *,
        target_name: "str",
        target_type: "type[Any]",
        method_name: "str",
    ) -> "tuple[Request[Any, Any, Any], Any]":
        remaining = dict(kwargs)
        kw_request = remaining.pop("request", _UNSET)
        kw_target = remaining.pop(target_name, _UNSET)
        if remaining:
            unexpected = next(iter(remaining))
            msg = f"{method_name}() got an unexpected keyword argument {unexpected!r}"
            raise TypeError(msg)
        if len(args) > _MAX_POSITIONAL_ARGS:
            msg = f"{method_name}() takes at most 2 positional arguments but {len(args)} were given"
            raise TypeError(msg)
        if len(args) == _MAX_POSITIONAL_ARGS:
            if kw_request is not _UNSET:
                msg = f"{method_name}() got multiple values for argument 'request'"
                raise TypeError(msg)
            if kw_target is not _UNSET:
                msg = f"{method_name}() got multiple values for argument {target_name!r}"
                raise TypeError(msg)
            req_arg, target = args
        elif len(args) == 1:
            req_arg, target = self._unpack_single_positional_arg(
                args[0],
                kw_request=kw_request,
                kw_target=kw_target,
                target_name=target_name,
                target_type=target_type,
                method_name=method_name,
            )
        elif kw_target is _UNSET:
            msg = f"{method_name}() missing required argument: {target_name!r}"
            raise TypeError(msg)
        else:
            req_arg = None if kw_request is _UNSET else kw_request
            target = kw_target
        if not isinstance(target, target_type):
            msg = (
                f"{method_name}() argument {target_name!r} must be {target_type.__name__}, got {type(target).__name__}"
            )
            raise TypeError(msg)
        return self._resolve_request(req_arg), target

    @staticmethod
    def _unpack_single_positional_arg(
        first: "Any",
        *,
        kw_request: "Any",
        kw_target: "Any",
        target_name: "str",
        target_type: "type[Any]",
        method_name: "str",
    ) -> "tuple[Any, Any]":
        if kw_target is not _UNSET:
            if kw_request is not _UNSET:
                msg = f"{method_name}() got multiple values for argument 'request'"
                raise TypeError(msg)
            return first, kw_target
        if kw_request is not _UNSET:
            return kw_request, first
        if isinstance(first, target_type):
            return None, first
        msg = f"{method_name}() missing required argument: {target_name!r}"
        raise TypeError(msg)


def parse_frontmatter(text: "str") -> "dict[str, Any]":
    """Parse the leading ``---`` YAML frontmatter block of a ``SKILL.md`` document."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        msg = "SKILL.md must begin with a '---' YAML frontmatter fence"
        raise ImproperlyConfiguredException(msg)

    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        msg = "SKILL.md frontmatter is missing a closing '---' fence"
        raise ImproperlyConfiguredException(msg)

    yaml_block = "\n".join(lines[1:closing_index])
    try:
        loaded = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        msg = f"SKILL.md frontmatter is not valid YAML: {exc}"
        raise ImproperlyConfiguredException(msg) from exc

    if not isinstance(loaded, dict):
        msg = "SKILL.md frontmatter must parse to a YAML mapping"
        raise ImproperlyConfiguredException(msg)

    return dict(loaded)


def parse_skill_uri(uri: "str") -> "tuple[str, str] | None":
    """Split ``skill://<name>[/<relative_path>]`` into ``(name, relative_path)`` or ``None``."""
    if not isinstance(uri, str) or not uri.startswith(SKILL_URI_PREFIX):
        return None
    remainder = uri[len(SKILL_URI_PREFIX) :]
    if not remainder or remainder.endswith("/") or "\\" in remainder:
        return None

    parts = remainder.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None

    name = parts[0]
    relative_path = "/".join(parts[1:]) if len(parts) > 1 else ""
    return name, relative_path


def load_skill(
    skill_dir: "Path",
    *,
    max_files: "int",
    max_bytes: "int",
) -> "Skill":
    """Load and validate a single skill directory."""
    skill_md_path = skill_dir / "SKILL.md"
    try:
        skill_md_text = skill_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"Failed to read {skill_md_path}: {exc}"
        raise ImproperlyConfiguredException(msg) from exc

    frontmatter = parse_frontmatter(skill_md_text)
    fm_name = frontmatter.get("name")
    if not isinstance(fm_name, str) or fm_name != skill_dir.name:
        msg = f"Skill in {skill_dir} has frontmatter name {fm_name!r}, which does not match folder name {skill_dir.name!r}"
        raise ImproperlyConfiguredException(msg)

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        msg = f"Skill '{skill_dir.name}' in {skill_dir} must declare a non-empty 'description' in frontmatter"
        raise ImproperlyConfiguredException(msg)

    collected_paths: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        current_dir = Path(dirpath)
        dirnames[:] = [
            dirname for dirname in dirnames if not dirname.startswith(".") and not (current_dir / dirname).is_symlink()
        ]
        for filename in filenames:
            if filename.startswith("."):
                continue
            candidate = current_dir / filename
            if candidate.is_symlink() or not candidate.is_file():
                continue
            rel = candidate.relative_to(skill_dir).as_posix()
            collected_paths.append((rel, candidate.resolve()))

    if len(collected_paths) > max_files:
        msg = (
            f"Skill '{skill_dir.name}' contains {len(collected_paths)} files, exceeding max_files_per_skill={max_files}"
        )
        raise ImproperlyConfiguredException(msg)

    collected_paths.sort(key=lambda item: item[0])

    total_bytes = 0
    skill_files: list[SkillFile] = []
    for relative_path, resolved_path in collected_paths:
        try:
            data = resolved_path.read_bytes()
        except OSError as exc:
            msg = f"Failed to read skill file {resolved_path}: {exc}"
            raise ImproperlyConfiguredException(msg) from exc

        file_size = len(data)
        total_bytes += file_size
        if total_bytes > max_bytes:
            msg = f"Skill '{skill_dir.name}' exceeds max_bytes_per_skill={max_bytes} (reached {total_bytes} bytes)"
            raise ImproperlyConfiguredException(msg)

        skill_files.append(
            SkillFile(
                relative_path=relative_path,
                path=resolved_path,
                uri=f"{SKILL_URI_PREFIX}{skill_dir.name}/{relative_path}",
                mime_type=guess_skill_mime_type(relative_path),
                size=file_size,
                digest=compute_digest(data),
            )
        )

    return Skill(
        name=skill_dir.name,
        description=description,
        uri=f"{SKILL_URI_PREFIX}{skill_dir.name}/SKILL.md",
        frontmatter=frontmatter,
        files=tuple(skill_files),
    )


def _blank_skill_request(app_ref: "Litestar | None") -> "Request[Any, Any, Any]":
    scope: dict[str, Any] = {
        "type": "http",
        "app": app_ref,
        "litestar_app": app_ref,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("mcp-internal", 0),
        "client": ("mcp-internal", 0),
        "state": {},
        "session": {},
        "user": None,
        "auth": None,
        "extensions": {},
    }
    return Request(cast("Any", scope))


def _normalize_title(title: "Any") -> "str | None":
    if not isinstance(title, str):
        return None
    stripped = title.strip()
    return stripped or None


def _copy_icons(icons: "Any") -> "list[dict[str, Any]] | None":
    if icons is None or isinstance(icons, (str, bytes)) or not isinstance(icons, Sequence):
        return None
    copied = [copy.deepcopy(dict(item)) for item in icons if isinstance(item, Mapping)]
    return copied or None


def _normalize_tags(tags: "Any") -> "tuple[str, ...]":
    if isinstance(tags, (str, bytes)) or not isinstance(tags, Sequence):
        msg = "tags must be a sequence of strings"
        raise TypeError(msg)
    normalized: list[str] = []
    for item in tags:
        if not isinstance(item, str):
            msg = f"tags entries must be strings, got {type(item).__name__}"
            raise TypeError(msg)
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return tuple(normalized)


def _extract_frontmatter_tags(raw_tags: "Any") -> "tuple[str, ...]":
    if not isinstance(raw_tags, (list, tuple)):
        return ()
    return tuple(item.strip() for item in raw_tags if isinstance(item, str) and item.strip())


def _copy_meta(meta: "Any") -> "dict[str, Any] | None":
    if not isinstance(meta, Mapping):
        return None
    copied = copy.deepcopy(dict(meta))
    return copied or None


def _validate_skill_name(name: "str") -> "None":
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
        msg = f"Invalid skill name: {name!r}"
        raise ValueError(msg)


def _validate_relative_path(relative_path: "str") -> "None":
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or relative_path.startswith("/")
        or relative_path.endswith("/")
        or "\\" in relative_path
    ):
        msg = f"Invalid skill file relative_path: {relative_path!r}"
        raise ValueError(msg)
    parts = relative_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        msg = f"Invalid skill file relative_path: {relative_path!r}"
        raise ValueError(msg)


def _is_valid_sha256_digest(digest: "str") -> "bool":
    return (
        isinstance(digest, str)
        and digest.startswith(_SHA256_DIGEST_PREFIX)
        and len(digest) == _SHA256_DIGEST_LENGTH
        and all(ch in _DIGEST_HEX_CHARS for ch in digest[len(_SHA256_DIGEST_PREFIX) :])
    )
