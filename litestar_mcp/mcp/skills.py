"""Filesystem-backed catalog for the Skills over MCP extension."""

import hashlib
import mimetypes
import os
from collections.abc import Sequence  # noqa: TC003
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from litestar.exceptions import ImproperlyConfiguredException

from litestar_mcp.core.exceptions import LitestarMCPError
from litestar_mcp.mcp.config import MCPSkillsConfig  # noqa: TC001

SKILL_URI_PREFIX = "skill://"
SKILL_FILE_NAME = "SKILL.md"
SKILL_MARKDOWN_MIME_TYPE = "text/markdown"
DIRECTORY_MIME_TYPE = "inode/directory"


class SkillIntegrityError(LitestarMCPError):
    """Raised when a skill file on disk no longer matches its manifest digest."""


@dataclass(frozen=True, slots=True)
class SkillFile:
    """One file belonging to a :class:`Skill`, with its integrity manifest.

    Attributes:
        relative_path: POSIX path relative to the skill folder, e.g.
            ``"scripts/run.py"``.
        path: Absolute path to the file on disk.
        uri: ``skill://<name>/<relative_path>`` addressing this file.
        mime_type: MIME type used for MCP resource metadata.
        size: File size in bytes, captured at catalog build time.
        digest: ``"sha256:<64 lowercase hex>"`` of the file contents at
            catalog build time.
    """

    relative_path: "str"
    path: "Path"
    uri: "str"
    mime_type: "str"
    size: "int"
    digest: "str"

    def to_manifest_entry(self) -> "dict[str, Any]":
        """Return the compact manifest entry used in ``Skill.to_entry``."""
        return {"uri": self.uri, "digest": self.digest, "size": self.size}

    def to_resource(self, skill: "Skill") -> "dict[str, Any]":
        """Return the MCP Resource object for ``resources/list`` and directory reads."""
        resource: dict[str, Any] = {
            "uri": self.uri,
            "name": self.relative_path,
            "mimeType": self.mime_type,
            "size": self.size,
        }
        if self.relative_path == SKILL_FILE_NAME:
            resource["description"] = skill.description
        return resource


@dataclass(frozen=True, slots=True)
class Skill:
    """One skill folder loaded from disk.

    Attributes:
        name: Skill name, matching both the folder name and the
            frontmatter ``name`` field.
        description: Human-readable description from the frontmatter.
        uri: ``skill://<name>/SKILL.md`` addressing the skill's manifest.
        frontmatter: Parsed YAML mapping from ``SKILL.md``, unchanged.
        files: All files belonging to the skill, including ``SKILL.md``,
            sorted by ``relative_path``.
    """

    name: "str"
    description: "str"
    uri: "str"
    frontmatter: "dict[str, Any]"
    files: "tuple[SkillFile, ...]"

    def to_entry(self) -> "dict[str, Any]":
        """Return the ``skills/list`` entry for this skill."""
        return {
            "uri": self.uri,
            "frontmatter": self.frontmatter,
            "resources": [file.to_manifest_entry() for file in self.files],
        }


class SkillCatalog:
    """Immutable, deterministic catalog of skills loaded from disk."""

    def __init__(self, skills: "Sequence[Skill]") -> "None":
        """Store ``skills`` sorted by name and build URI lookup indexes.

        Args:
            skills: Skills to include in the catalog.
        """
        self._skills: tuple[Skill, ...] = tuple(sorted(skills, key=lambda skill: skill.name))
        self._by_uri: dict[str, Skill] = {skill.uri: skill for skill in self._skills}
        self._files_by_uri: dict[str, tuple[Skill, SkillFile]] = {
            file.uri: (skill, file) for skill in self._skills for file in skill.files
        }

    @classmethod
    def from_config(cls, config: "MCPSkillsConfig") -> "SkillCatalog":
        """Build a catalog from every configured path.

        Args:
            config: Skills extension configuration.

        Returns:
            The assembled, immutable catalog.

        Raises:
            ImproperlyConfiguredException: A configured path is not a
                directory, a skill fails validation, or a skill name is
                duplicated across paths.
        """
        skills: list[Skill] = []
        seen_names: dict[str, Path] = {}
        for raw_path in config.paths:
            root = Path(raw_path).resolve()
            if not root.is_dir():
                msg = f"Skills path {root} is not a directory"
                raise ImproperlyConfiguredException(msg)
            for child in sorted(root.iterdir(), key=lambda path: path.name):
                if not child.is_dir() or not (child / SKILL_FILE_NAME).is_file():
                    continue
                skill = load_skill(
                    child,
                    max_files=config.max_files_per_skill,
                    max_bytes=config.max_bytes_per_skill,
                )
                if skill.name in seen_names:
                    msg = f"Duplicate skill name {skill.name!r} found at {child} and {seen_names[skill.name]}"
                    raise ImproperlyConfiguredException(msg)
                seen_names[skill.name] = child
                skills.append(skill)
        return cls(skills)

    @property
    def skills(self) -> "tuple[Skill, ...]":
        """Return every loaded skill, sorted by name."""
        return self._skills

    def get(self, uri: "str") -> "Skill | None":
        """Return the skill whose ``SKILL.md`` URI is ``uri``, if any."""
        return self._by_uri.get(uri)

    def get_file(self, uri: "str") -> "tuple[Skill, SkillFile] | None":
        """Return the ``(skill, file)`` pair addressed by ``uri``, if any."""
        return self._files_by_uri.get(uri)

    def list_directory(self, uri: "str") -> "list[dict[str, Any]] | None":
        """List the files and subdirectories directly under ``uri``.

        Args:
            uri: A ``skill://`` URI naming the skill and, optionally, a
                directory prefix within it.

        Returns:
            Directory entries sorted by name, or ``None`` when ``uri``
            does not name an existing directory.
        """
        parsed = parse_skill_uri(uri)
        if parsed is None:
            return None
        name, relative = parsed
        skill = self._by_uri.get(f"{SKILL_URI_PREFIX}{name}/{SKILL_FILE_NAME}")
        if skill is None:
            return None
        prefix = f"{relative}/" if relative else ""
        entries: dict[str, dict[str, Any]] = {}
        found = False
        for file in skill.files:
            if not file.relative_path.startswith(prefix):
                continue
            rest = file.relative_path[len(prefix) :]
            if not rest:
                continue
            found = True
            if "/" not in rest:
                entries[rest] = file.to_resource(skill)
            else:
                segment = rest.split("/", 1)[0]
                entries.setdefault(
                    segment,
                    {
                        "uri": f"{SKILL_URI_PREFIX}{name}/{prefix}{segment}",
                        "name": segment,
                        "mimeType": DIRECTORY_MIME_TYPE,
                    },
                )
        if not found and prefix:
            return None
        return sorted(entries.values(), key=lambda entry: entry["name"])

    def resource_entries(self) -> "list[dict[str, Any]]":
        """Return every file of every skill as an MCP Resource object."""
        return [file.to_resource(skill) for skill in self._skills for file in skill.files]

    def read_file(self, file: "SkillFile") -> "bytes":
        """Read ``file`` from disk and verify it still matches its digest.

        Args:
            file: The file to read.

        Returns:
            The file's current contents.

        Raises:
            SkillIntegrityError: The file's contents no longer match the
                digest captured when the catalog was built.
        """
        data = file.path.read_bytes()
        if compute_digest(data) != file.digest:
            msg = f"{file.uri} changed on disk since startup"
            raise SkillIntegrityError(msg)
        return data


def compute_digest(data: "bytes") -> "str":
    """Return the ``sha256:<hex>`` digest of ``data``."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def parse_frontmatter(text: "str") -> "dict[str, Any]":
    """Parse the YAML frontmatter block from a ``SKILL.md`` file.

    Args:
        text: Full contents of the ``SKILL.md`` file.

    Returns:
        The parsed YAML mapping.

    Raises:
        ImproperlyConfiguredException: The text does not start with a
            ``---`` fence, has no closing fence, or the frontmatter block
            does not parse to a mapping.
    """
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        msg = "SKILL.md must start with YAML frontmatter"
        raise ImproperlyConfiguredException(msg)
    lines = normalized.split("\n")
    closing_index = None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            closing_index = index
            break
    if closing_index is None:
        msg = "SKILL.md must start with YAML frontmatter"
        raise ImproperlyConfiguredException(msg)
    block = "\n".join(lines[1:closing_index])
    parsed = yaml.safe_load(block)
    if not isinstance(parsed, dict):
        msg = "SKILL.md frontmatter must be a YAML mapping"
        raise ImproperlyConfiguredException(msg)
    return parsed


def parse_skill_uri(uri: "str") -> "tuple[str, str] | None":
    """Split a ``skill://`` URI into ``(name, relative_path)``.

    Args:
        uri: The candidate URI.

    Returns:
        ``(name, relative_path)`` where ``relative_path`` is ``""`` for a
        bare skill URI, or ``None`` when ``uri`` is not a valid skill URI.
    """
    if not uri.startswith(SKILL_URI_PREFIX):
        return None
    remainder = uri[len(SKILL_URI_PREFIX) :]
    if not remainder or remainder.endswith("/"):
        return None
    name, _, relative = remainder.partition("/")
    if not name or name in {".", ".."}:
        return None
    if relative:
        for segment in relative.split("/"):
            if not segment or segment in {".", ".."}:
                return None
    return name, relative


def load_skill(directory: "Path", *, max_files: "int", max_bytes: "int") -> "Skill":
    """Load one skill folder from disk into a :class:`Skill`.

    Args:
        directory: The skill's folder, containing ``SKILL.md``.
        max_files: Maximum number of files, including ``SKILL.md``, the
            skill may contain.
        max_bytes: Maximum total byte size of the skill's files.

    Returns:
        The loaded skill.

    Raises:
        ImproperlyConfiguredException: The frontmatter is invalid, the
            ``name``/``description`` fields are missing or empty, the
            frontmatter ``name`` does not match the folder name, or the
            skill exceeds ``max_files``/``max_bytes``.
    """
    skill_md_path = directory / SKILL_FILE_NAME
    skill_md_bytes = skill_md_path.read_bytes()
    frontmatter = parse_frontmatter(skill_md_bytes.decode())

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not name:
        msg = f"Skill at {directory} must declare a non-empty string 'name' in its frontmatter"
        raise ImproperlyConfiguredException(msg)
    if not isinstance(description, str) or not description:
        msg = f"Skill {name!r} must declare a non-empty string 'description' in its frontmatter"
        raise ImproperlyConfiguredException(msg)
    if name != directory.name:
        msg = f"Skill frontmatter name {name!r} must match its folder name {directory.name!r}"
        raise ImproperlyConfiguredException(msg)

    file_paths: list[Path] = []
    for current_dir, dirnames, filenames in os.walk(directory, followlinks=False):
        current_path = Path(current_dir)
        dirnames[:] = sorted(
            dirname for dirname in dirnames if not dirname.startswith(".") and not (current_path / dirname).is_symlink()
        )
        for filename in filenames:
            file_path = current_path / filename
            if filename.startswith(".") or file_path.is_symlink() or not file_path.is_file():
                continue
            file_paths.append(file_path)

    file_paths.sort(key=lambda path: path.relative_to(directory).as_posix())

    if len(file_paths) > max_files:
        msg = f"Skill {name!r} has more than the allowed {max_files} files"
        raise ImproperlyConfiguredException(msg)

    stat_sizes = [path.stat().st_size for path in file_paths]
    if sum(stat_sizes) > max_bytes:
        msg = f"Skill {name!r} exceeds the allowed {max_bytes} total bytes"
        raise ImproperlyConfiguredException(msg)

    files: list[SkillFile] = []
    for file_path in file_paths:
        relative_path = file_path.relative_to(directory).as_posix()
        data = skill_md_bytes if relative_path == SKILL_FILE_NAME else file_path.read_bytes()
        size = len(data)
        mime_type = (
            SKILL_MARKDOWN_MIME_TYPE
            if relative_path == SKILL_FILE_NAME
            else (mimetypes.guess_type(relative_path)[0] or "application/octet-stream")
        )
        files.append(
            SkillFile(
                relative_path=relative_path,
                path=file_path,
                uri=f"{SKILL_URI_PREFIX}{name}/{relative_path}",
                mime_type=mime_type,
                size=size,
                digest=compute_digest(data),
            ),
        )

    return Skill(
        name=name,
        description=description,
        uri=f"{SKILL_URI_PREFIX}{name}/{SKILL_FILE_NAME}",
        frontmatter=frontmatter,
        files=tuple(files),
    )
