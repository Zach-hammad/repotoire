"""Database package for Repotoire SaaS platform.

This package contains SQLAlchemy models and database utilities for the
multi-tenant SaaS application.

Subpackages:
    models: SQLAlchemy ORM models
    session: Async database session management
"""

from .models import (
    AnalysisRun,
    AnalysisStatus,
    Base,
    GitHubInstallation,
    GitHubRepository,
    MemberRole,
    Organization,
    OrganizationMembership,
    PlanTier,
    Repository,
    TimestampMixin,
    User,
    UUIDPrimaryKeyMixin,
)
from .session import close_db, get_db, init_db

__all__ = [
    # Base classes
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    # Models
    "User",
    "Organization",
    "OrganizationMembership",
    "Repository",
    "AnalysisRun",
    "GitHubInstallation",
    "GitHubRepository",
    # Enums
    "PlanTier",
    "MemberRole",
    "AnalysisStatus",
    # Session management
    "get_db",
    "init_db",
    "close_db",
]
