"""Opaque cursor encoding shared by paginated list endpoints.

The package-private leading underscore marks this module as internal: the
encoding is an implementation detail and clients must treat cursors as opaque.
"""

import base64
import binascii


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        offset = int(raw)
    except (ValueError, binascii.Error) as exc:
        msg = "Invalid cursor"
        raise ValueError(msg) from exc
    if offset < 0:
        msg = "Invalid cursor"
        raise ValueError(msg)
    return offset
