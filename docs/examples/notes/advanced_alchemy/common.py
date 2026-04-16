"""Shared Advanced Alchemy model and service for the reference notes examples."""

from advanced_alchemy.base import UUIDAuditBase
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from sqlalchemy.orm import Mapped, mapped_column


class NoteRecord(UUIDAuditBase):
    """Advanced Alchemy model for the reference notes examples."""

    __tablename__ = "reference_notes"

    title: Mapped[str] = mapped_column()
    body: Mapped[str] = mapped_column()
    owner_sub: Mapped[str | None] = mapped_column(default=None, index=True)


class NoteService(SQLAlchemyAsyncRepositoryService[NoteRecord]):
    """Service layer for the reference notes examples."""

    class Repo(SQLAlchemyAsyncRepository[NoteRecord]):
        model_type = NoteRecord

    repository_type = Repo
