"""Public helper types for MCP tool content blocks."""

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from litestar.serialization import encode_json

_CONTENT_BLOCK_REQUIRED_KEYS: "dict[str, frozenset[str]]" = {
    "text": frozenset({"text"}),
    "image": frozenset({"data", "mimeType"}),
    "audio": frozenset({"data", "mimeType"}),
    "resource_link": frozenset({"uri", "name"}),
    "resource": frozenset({"resource"}),
}


@dataclass(frozen=True, slots=True)
class MCPResourceLink:
    """A tool-result content block linking to a fetchable MCP resource."""

    name: "str"
    uri: "str"
    mime_type: "str | None" = None
    description: "str | None" = None
    title: "str | None" = None
    size: "int | None" = None
    annotations: "dict[str, Any] | None" = None
    meta: "dict[str, Any] | None" = None

    def to_content_block(self) -> "dict[str, Any]":
        """Return the MCP ``resource_link`` content block."""
        block: dict[str, Any] = {"type": "resource_link", "name": self.name, "uri": self.uri}
        if self.mime_type is not None:
            block["mimeType"] = self.mime_type
        if self.description is not None:
            block["description"] = self.description
        if self.title is not None:
            block["title"] = self.title
        if self.size is not None:
            block["size"] = self.size
        if self.annotations is not None:
            block["annotations"] = self.annotations
        if self.meta is not None:
            block["_meta"] = self.meta
        return block


@dataclass(frozen=True, slots=True)
class MCPBlobResource:
    """A tool-result embedded resource backed by bytes."""

    uri: "str"
    data: "bytes | bytearray | memoryview"
    mime_type: "str" = "application/octet-stream"
    annotations: "dict[str, Any] | None" = None
    meta: "dict[str, Any] | None" = None

    def to_content_block(self, *, max_blob_bytes: "int | None" = None) -> "dict[str, Any]":
        """Return the MCP embedded resource content block.

        Args:
            max_blob_bytes: Optional byte limit enforced before base64 encoding.
        """
        payload = bytes(self.data)
        enforce_blob_size(len(payload), max_blob_bytes=max_blob_bytes)
        resource: dict[str, Any] = {
            "uri": self.uri,
            "mimeType": self.mime_type,
            "blob": base64.b64encode(payload).decode("ascii"),
        }
        if self.annotations is not None:
            resource["annotations"] = self.annotations
        if self.meta is not None:
            resource["_meta"] = self.meta
        return {"type": "resource", "resource": resource}


@dataclass(frozen=True, slots=True)
class MCPToolResult:
    """A complete MCP tool result with explicit content blocks."""

    content: "Any"
    structured_content: "dict[str, Any] | None" = None
    is_error: "bool" = False
    meta: "dict[str, Any] | None" = None

    def to_result(self, *, max_blob_bytes: "int | None" = None, task_id: "str | None" = None) -> "dict[str, Any]":
        """Return the MCP ``tools/call`` result object."""
        result: dict[str, Any] = {
            "content": normalize_content_blocks(self.content, max_blob_bytes=max_blob_bytes),
            "isError": self.is_error,
        }
        if self.structured_content is not None:
            result["structuredContent"] = self.structured_content
        if self.meta is not None:
            result["_meta"] = dict(self.meta)
        add_related_task_meta(result, task_id)
        return result


def enforce_blob_size(size: "int", *, max_blob_bytes: "int | None") -> "None":
    """Raise when ``size`` exceeds the configured blob cap."""
    if max_blob_bytes is not None and size > max_blob_bytes:
        msg = f"blob size {size} exceeds configured max_blob_bytes {max_blob_bytes}"
        raise ValueError(msg)


def add_related_task_meta(result: "dict[str, Any]", task_id: "str | None") -> "None":
    """Attach MCP task metadata to a result object."""
    if task_id is None:
        return
    meta = result.setdefault("_meta", {})
    meta["io.modelcontextprotocol/related-task"] = {"taskId": task_id}


def is_content_block(value: "Any") -> "bool":
    """Return whether ``value`` already looks like an MCP content block."""
    if not isinstance(value, dict):
        return False
    variant = value.get("type")
    required = _CONTENT_BLOCK_REQUIRED_KEYS.get(variant) if isinstance(variant, str) else None
    return required is not None and required.issubset(value.keys())


def normalize_content_blocks(value: "Any", *, max_blob_bytes: "int | None" = None) -> "list[dict[str, Any]]":
    """Normalize helper objects, strings, and content-block dicts."""
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, memoryview, dict)):
        return [normalize_content_block(item, max_blob_bytes=max_blob_bytes) for item in value]
    return [normalize_content_block(value, max_blob_bytes=max_blob_bytes)]


def normalize_content_block(value: "Any", *, max_blob_bytes: "int | None" = None) -> "dict[str, Any]":
    """Normalize one value to an MCP content block."""
    if isinstance(value, MCPResourceLink):
        return value.to_content_block()
    if isinstance(value, MCPBlobResource):
        return value.to_content_block(max_blob_bytes=max_blob_bytes)
    if is_content_block(value):
        block = dict(value)
        _enforce_dict_blob_size(block, max_blob_bytes=max_blob_bytes)
        return block
    if isinstance(value, str):
        return {"type": "text", "text": value}
    return {"type": "text", "text": encode_json(value).decode("utf-8")}


def _enforce_dict_blob_size(block: "dict[str, Any]", *, max_blob_bytes: "int | None") -> "None":
    if max_blob_bytes is None or block.get("type") != "resource":
        return
    resource = block.get("resource")
    if not isinstance(resource, dict):
        return
    blob = resource.get("blob")
    if not isinstance(blob, str):
        return
    try:
        size = len(base64.b64decode(blob, validate=True))
    except ValueError:
        return
    enforce_blob_size(size, max_blob_bytes=max_blob_bytes)


__all__ = (
    "MCPBlobResource",
    "MCPResourceLink",
    "MCPToolResult",
    "add_related_task_meta",
    "enforce_blob_size",
    "is_content_block",
    "normalize_content_block",
    "normalize_content_blocks",
)
