"""Filesystem and dynamic skill sources and catalog for the Skills over MCP extension."""

import hashlib
import logging
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import anyio
import yaml
from litestar.exceptions import ImproperlyConfiguredException

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from litestar import Request

    from litestar_mcp.mcp.config import MCPSkillsConfig

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
    """One file inside a skill folder or dynamic skill manifest.

    Attributes:
        relative_path: Forward-slash path relative to the skill root.
        path: Absolute filesystem path on disk, or ``None`` for in-memory/lazy files.
        uri: Canonical ``skill://<name>/<relative_path>`` URI.
        mime_type: Resolved MIME type (``application/octet-stream`` fallback).
        size: Byte length recorded in the skill manifest.
        digest: ``sha256:<hex>`` recorded in the skill manifest.
        content: Optional inline byte payload served without calling ``source.read_file``.
    """

    relative_path: "str"
    path: "Path | None"
    uri: "str"
    mime_type: "str"
    size: "int"
    digest: "str"
    content: "bytes | None" = field(default=None, repr=False)

    def __post_init__(self) -> "None":
        """Validate path, URI, size, digest, and inline content invariants."""
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

    @classmethod
    def from_content(cls, skill_name: "str", relative_path: "str", content: "str | bytes") -> "SkillFile":
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
        )

    def to_manifest_entry(self) -> "dict[str, Any]":
        """Return the ``skills/list`` / ``skills/get`` resource manifest entry."""
        return {
            "uri": self.uri,
            "name": self.relative_path,
            "mimeType": self.mime_type,
            "size": self.size,
            "digest": self.digest,
        }

    def to_resource(self, skill: "Skill") -> "dict[str, Any]":
        """Return the ``resources/list`` entry for this file within ``skill``."""
        entry: dict[str, Any] = {
            "uri": self.uri,
            "name": self.relative_path,
            "mimeType": self.mime_type,
            "size": self.size,
        }
        if self.relative_path == "SKILL.md":
            entry["description"] = skill.description
        return entry


def verify_skill_file(file: "SkillFile", data: "bytes") -> "bytes":
    """Verify ``data`` matches ``file.size`` and ``file.digest``, or raise ``SkillIntegrityError``."""
    if len(data) != file.size or compute_digest(data) != file.digest:
        msg = f"Skill file failed integrity verification: {file.uri}"
        raise SkillIntegrityError(msg)
    return data


@dataclass(frozen=True, slots=True)
class Skill:
    """One skill loaded from a skill directory or constructed dynamically.

    Attributes:
        name: Skill identifier, matching the ``name`` frontmatter key.
        description: Human-readable summary from frontmatter.
        uri: Canonical ``skill://<name>/SKILL.md`` URI.
        frontmatter: Full parsed YAML frontmatter mapping, preserved verbatim.
        files: Every file belonging to the skill, sorted by ``relative_path``.
    """

    name: "str"
    description: "str"
    uri: "str"
    frontmatter: "dict[str, Any]"
    files: "tuple[SkillFile, ...]"

    def __post_init__(self) -> "None":
        """Validate skill identity, frontmatter consistency, file URIs, and sort files."""
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

        sorted_files = tuple(sorted(self.files, key=lambda item: item.relative_path))
        object.__setattr__(self, "files", sorted_files)

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
            fm_mapping.update(metadata)
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
        return {
            "uri": self.uri,
            "frontmatter": dict(self.frontmatter),
            "resources": [file.to_manifest_entry() for file in self.files],
        }

    def to_resources(self) -> "list[dict[str, Any]]":
        """Return the ``resources/list`` entries for every file in this skill."""
        return [file.to_resource(self) for file in self.files]


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
    skill: "Skill"
    file: "SkillFile"


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
    """Request-aware composition of :class:`SkillSource` instances."""

    __slots__ = ("_sources", "directory_read")

    def __init__(self, sources: "Sequence[SkillSource]", *, directory_read: "bool" = True) -> "None":
        self._sources: tuple[SkillSource, ...] = tuple(sources)
        self.directory_read = directory_read

    @property
    def sources(self) -> "tuple[SkillSource, ...]":
        """Tuple of composed :class:`SkillSource` instances in priority order."""
        return self._sources

    @classmethod
    def from_config(cls, config: "MCPSkillsConfig") -> "SkillCatalog":
        """Build a catalog from ``config`` (`paths` first as :class:`FilesystemSkillSource`, then ``sources``)."""
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
        return cls(sources=composed, directory_read=config.directory_read)

    async def list_skills(self, request: "Request[Any, Any, Any]") -> "list[Skill]":
        """Return every skill visible to ``request`` across all sources, deduplicated and sorted by name."""
        by_name: dict[str, Skill] = {}
        for source in self._sources:
            source_skills = await source.list_skills(request)
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
        return sorted(by_name.values(), key=lambda item: item.name)

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

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str") -> "Skill | None":
        """Return the skill named ``name`` visible to ``request``, or ``None``."""
        resolved = await self._resolve_skill_with_source(request, name)
        return resolved[1] if resolved is not None else None

    async def get(self, request: "Request[Any, Any, Any]", uri: "str") -> "Skill | None":
        """Return the skill whose canonical ``skill://<name>/SKILL.md`` URI equals ``uri``, else ``None``."""
        parsed = parse_skill_uri(uri)
        if parsed is None:
            return None
        name, relative_path = parsed
        if relative_path != "SKILL.md":
            return None
        return await self.get_skill(request, name)

    async def get_file(self, request: "Request[Any, Any, Any]", uri: "str") -> "SkillFileMatch | None":
        """Resolve ``uri`` to a :class:`SkillFileMatch` visible to ``request``, or ``None``."""
        parsed = parse_skill_uri(uri)
        if parsed is None:
            return None
        name, relative_path = parsed
        if not relative_path:
            return None
        resolved = await self._resolve_skill_with_source(request, name)
        if resolved is None:
            return None
        source, skill = resolved
        skill_file = skill.get_file(relative_path)
        if skill_file is None:
            return None
        return SkillFileMatch(source=source, skill=skill, file=skill_file)

    async def list_directory(self, request: "Request[Any, Any, Any]", uri: "str") -> "list[dict[str, Any]] | None":
        """List direct file and directory children under ``uri``, or ``None`` if not a visible directory."""
        parsed = parse_skill_uri(uri)
        if parsed is None:
            return None
        name, relative_path = parsed
        skill = await self.get_skill(request, name)
        if skill is None:
            return None
        return skill.list_directory(relative_path)

    async def resource_entries(self, request: "Request[Any, Any, Any]") -> "list[dict[str, Any]]":
        """Return ``resources/list`` entries for every file in every visible skill."""
        entries: list[dict[str, Any]] = []
        for skill in await self.list_skills(request):
            entries.extend(skill.to_resources())
        return entries

    async def read_file(self, request: "Request[Any, Any, Any]", match: "SkillFileMatch") -> "bytes":
        """Read ``match.file`` (inline or via ``match.source.read_file``) and verify size and digest."""
        if match.file.content is not None:
            data = match.file.content
        else:
            data = await match.source.read_file(request, match.skill, match.file)
        return await anyio.to_thread.run_sync(verify_skill_file, match.file, data)


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
