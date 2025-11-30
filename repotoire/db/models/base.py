"""SQLAlchemy base model with common fields and mixins.

This module defines the base class and mixins for all SQLAlchemy models
in the Repotoire SaaS platform.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models.

    Provides common configuration and type annotation support.
    """

    pass


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamp columns.

    Automatically sets created_at on insert and updates updated_at on every update.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """Mixin that adds a UUID primary key column.

    Uses uuid4 for generating unique identifiers.
    """

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )


def generate_repr(obj: Any, *attrs: str) -> str:
    """Generate a repr string for a model instance.

    Args:
        obj: The model instance
        *attrs: Attribute names to include in repr

    Returns:
        A formatted repr string
    """
    class_name = obj.__class__.__name__
    attr_strs = [f"{attr}={getattr(obj, attr, None)!r}" for attr in attrs]
    return f"<{class_name} {' '.join(attr_strs)}>"
