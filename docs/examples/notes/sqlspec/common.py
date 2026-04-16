"""Shared SQLSpec wiring for the reference notes examples.

This module owns the SQLSpec adapter configuration, the typed row mapping,
the schema bootstrap SQL, and the :class:`SQLSpecNoteService` used by every
variant in the family. Variant files never redeclare SQL or row shapes —
they compose this module's service with a different DI/auth surface.

The adapter deliberately stays concrete (``AiosqliteConfig``) and all SQL is
parameterized via SQLite's qmark (``?``) binding so the example teaches the
SQLSpec contract rather than hiding it behind an ORM.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final
from uuid import UUID, uuid4

import msgspec
from sqlspec import SQLSpec
from sqlspec.adapters.aiosqlite import AiosqliteConfig

TABLE_NAME: Final = "reference_notes_sqlspec"

CREATE_TABLE_SQL: Final = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    owner_sub TEXT
)
"""

INSERT_SQL: Final = f"INSERT INTO {TABLE_NAME} (id, title, body, owner_sub) VALUES (?, ?, ?, ?)"
SELECT_ALL_SQL: Final = f"SELECT id, title, body, owner_sub FROM {TABLE_NAME} ORDER BY id"
SELECT_BY_OWNER_SQL: Final = f"SELECT id, title, body, owner_sub FROM {TABLE_NAME} WHERE owner_sub = ? ORDER BY id"
SELECT_PUBLIC_SQL: Final = f"SELECT id, title, body, owner_sub FROM {TABLE_NAME} WHERE owner_sub IS NULL ORDER BY id"
SELECT_ONE_SQL: Final = f"SELECT id, title, body, owner_sub FROM {TABLE_NAME} WHERE id = ?"
SELECT_ONE_FOR_OWNER_SQL: Final = f"SELECT id, title, body, owner_sub FROM {TABLE_NAME} WHERE id = ? AND owner_sub = ?"
DELETE_SQL: Final = f"DELETE FROM {TABLE_NAME} WHERE id = ?"
DELETE_FOR_OWNER_SQL: Final = f"DELETE FROM {TABLE_NAME} WHERE id = ? AND owner_sub = ?"


class NoteRow(msgspec.Struct, kw_only=True):
    """Typed SQLSpec row shape for the reference notes table."""

    id: str
    title: str
    body: str
    owner_sub: str | None = None


class SQLSpecNoteService:
    """Thin, typed SQLSpec service for the reference notes family.

    The service accepts an already-provided async driver and exposes the
    handful of operations the shared notes contract needs: create, list
    (optionally owner-scoped), get, delete. All methods use bound
    parameters and :class:`NoteRow` result mapping.
    """

    __slots__ = ("driver",)

    def __init__(self, driver: object) -> None:
        self.driver = driver

    async def create(self, *, title: str, body: str, owner_sub: str | None = None) -> NoteRow:
        """Insert a note and return the typed row."""
        note_id = str(uuid4())
        await self.driver.execute(INSERT_SQL, note_id, title, body, owner_sub)  # type: ignore[attr-defined]
        await self.driver.commit()  # type: ignore[attr-defined]
        return await self.driver.select_one(SELECT_ONE_SQL, note_id, schema_type=NoteRow)  # type: ignore[attr-defined]

    async def list_public(self) -> list[NoteRow]:
        """List all notes without an owner (public demo dataset)."""
        return list(await self.driver.select(SELECT_PUBLIC_SQL, schema_type=NoteRow))  # type: ignore[attr-defined]

    async def list_for_owner(self, owner_sub: str) -> list[NoteRow]:
        """List notes owned by ``owner_sub``."""
        return list(
            await self.driver.select(SELECT_BY_OWNER_SQL, owner_sub, schema_type=NoteRow)  # type: ignore[attr-defined]
        )

    async def get_for_owner(self, note_id: str, owner_sub: str) -> NoteRow | None:
        """Return a single owned note or ``None`` when it does not exist."""
        rows = list(
            await self.driver.select(  # type: ignore[attr-defined]
                SELECT_ONE_FOR_OWNER_SQL, note_id, owner_sub, schema_type=NoteRow
            )
        )
        return rows[0] if rows else None

    async def delete(self, note_id: str) -> None:
        """Delete a note by id."""
        await self.driver.execute(DELETE_SQL, note_id)  # type: ignore[attr-defined]
        await self.driver.commit()  # type: ignore[attr-defined]

    async def delete_for_owner(self, note_id: str, owner_sub: str) -> bool:
        """Delete a note only if the caller owns it.

        Returns ``True`` when a row was deleted, ``False`` otherwise. The
        caller should never rely on this value to confirm the note existed
        for another principal — existence of another principal's note must
        never leak to an unauthorized caller.
        """
        existing = await self.get_for_owner(note_id, owner_sub)
        if existing is None:
            return False
        await self.driver.execute(DELETE_FOR_OWNER_SQL, note_id, owner_sub)  # type: ignore[attr-defined]
        await self.driver.commit()  # type: ignore[attr-defined]
        return True


def note_row_to_public(row: NoteRow) -> dict[str, object]:
    """Map a :class:`NoteRow` into the shared public ``Note`` shape.

    The shared contract expects ``id`` to be a :class:`~uuid.UUID`, so this
    helper normalizes the stored string identifier into the canonical
    contract shape. Variant files call this helper rather than constructing
    the public note inline.
    """
    return {"id": UUID(row.id), "title": row.title, "body": row.body}


def build_sqlspec(database_path: str) -> tuple[SQLSpec, AiosqliteConfig]:
    """Build a configured :class:`SQLSpec` instance for the notes family.

    Args:
        database_path: Filesystem path for the SQLite database file.

    Returns:
        A ``(SQLSpec, AiosqliteConfig)`` tuple. Callers hand the
        ``SQLSpec`` to :class:`SQLSpecPlugin` and keep the config around
        for dependency-provider session scoping.
    """
    sqlspec = SQLSpec()
    config = sqlspec.add_config(
        AiosqliteConfig(
            connection_config={"database": database_path},
            extension_config={"litestar": {"commit_mode": "autocommit"}},
        )
    )
    return sqlspec, config


@asynccontextmanager
async def provide_note_service(sqlspec: SQLSpec, config: AiosqliteConfig) -> AsyncIterator[SQLSpecNoteService]:
    """Yield a :class:`SQLSpecNoteService` bound to a request-scoped session."""
    async with sqlspec.provide_session(config) as db_session:
        yield SQLSpecNoteService(db_session)


async def bootstrap_schema(sqlspec: SQLSpec, config: AiosqliteConfig) -> None:
    """Create the notes table if it does not exist (idempotent)."""
    async with sqlspec.provide_session(config) as db_session:
        await db_session.execute(CREATE_TABLE_SQL)
        await db_session.commit()
