"""Unit tests for cursor encoding and decoding."""

import pytest

from litestar_mcp.core import decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    """Verify offset encoding and decoding round-trips correctly."""
    for offset in (0, 1, 42, 100, 9999):
        encoded = encode_cursor(offset)
        assert isinstance(encoded, str)
        assert decode_cursor(encoded) == offset


def test_cursor_invalid_values() -> None:
    """Verify malformed or negative cursors raise ValueError."""
    with pytest.raises(ValueError, match="Invalid cursor"):
        decode_cursor("not-base64!@#$")

    with pytest.raises(ValueError, match="Invalid cursor"):
        decode_cursor(encode_cursor(-5))
