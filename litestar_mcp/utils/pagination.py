"""Cursor pagination helpers for MCP list methods."""

import base64
import binascii
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")

DEFAULT_LIST_PAGE_SIZE = 100


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        offset = int(raw)
    except (UnicodeDecodeError, ValueError, binascii.Error) as exc:
        msg = "Invalid cursor"
        raise ValueError(msg) from exc
    if offset < 0:
        msg = "Invalid cursor"
        raise ValueError(msg)
    return offset


def paginate_items(
    items: Sequence[T],
    cursor: object | None,
    page_size: int = DEFAULT_LIST_PAGE_SIZE,
) -> tuple[list[T], str | None]:
    """Return one cursor-selected page and an optional next cursor."""
    if cursor is not None and not isinstance(cursor, str):
        msg = "Invalid cursor"
        raise ValueError(msg)
    if page_size <= 0:
        msg = "page_size must be positive"
        raise ValueError(msg)

    offset = _decode_cursor(cursor) if cursor is not None else 0
    next_offset = offset + page_size
    page = list(items[offset:next_offset])
    next_cursor = _encode_cursor(next_offset) if next_offset < len(items) else None
    return page, next_cursor
