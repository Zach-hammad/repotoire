"""Uptime tracking models for historical status data.

This module provides the UptimeRecord model for storing historical
health check data used to calculate uptime percentages.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin
from .status import ComponentStatus

if TYPE_CHECKING:
    from .status import StatusComponent


class UptimeRecord(Base, UUIDPrimaryKeyMixin):
    """Historical record of component health check results.

    Each record represents a single health check at a point in time.
    These records are used to calculate rolling uptime percentages
    and display historical status graphs.

    Attributes:
        id: UUID primary key
        component_id: FK to the component being tracked
        timestamp: When the health check was performed
        status: Status recorded at this time
        response_time_ms: Response time in milliseconds
        checked_by: Worker identifier that performed the check
        component: Parent component relationship
    """

    __tablename__ = "uptime_records"

    component_id: Mapped[UUID] = mapped_column(
        ForeignKey("status_components.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    status: Mapped[ComponentStatus] = mapped_column(
        Enum(
            ComponentStatus,
            name="component_status",
            values_callable=lambda x: [e.value for e in x],
            create_constraint=False,
        ),
        nullable=False,
    )
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_by: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    component: Mapped["StatusComponent"] = relationship(
        "StatusComponent",
        back_populates="uptime_records",
    )

    __table_args__ = (
        Index(
            "ix_uptime_records_component_timestamp",
            "component_id",
            "timestamp",
            postgresql_using="btree",
        ),
        Index(
            "ix_uptime_records_timestamp",
            "timestamp",
            postgresql_using="btree",
        ),
    )

    def __repr__(self) -> str:
        return f"<UptimeRecord component_id={self.component_id} status={self.status}>"
