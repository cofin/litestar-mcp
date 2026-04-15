"""Shared auth helpers for the reference notes examples."""

from typing import Any

import msgspec


class AuthenticatedIdentity(msgspec.Struct, kw_only=True):
    """Validated identity shared across JWT and Google IAP examples."""

    sub: str
    email: str | None = None


def _normalize_email(value: object) -> str | None:
    """Normalize provider-prefixed email values into plain email addresses."""
    if not isinstance(value, str) or not value:
        return None

    if ":" in value:
        _, suffix = value.split(":", 1)
        if "@" in suffix:
            return suffix

    return value


def identity_from_claims(claims: dict[str, Any]) -> AuthenticatedIdentity:
    """Map validated claims into the shared identity shape."""
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        msg = "Validated claims must include a non-empty 'sub' value"
        raise ValueError(msg)

    return AuthenticatedIdentity(
        sub=sub,
        email=_normalize_email(claims.get("email")),
    )
