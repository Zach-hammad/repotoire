"""Base classes for database migrations."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from repotoire.graph import Neo4jClient


class MigrationError(Exception):
    """Raised when a migration operation fails."""
    pass


class Migration(ABC):
    """Base class for database migrations.

    Each migration must implement up(), down(), and optionally validate().
    Migrations are applied in order by version number.
    """

    def __init__(self):
        """Initialize migration with metadata."""
        if not hasattr(self, 'version'):
            raise MigrationError(f"{self.__class__.__name__} must define 'version' attribute")
        if not hasattr(self, 'description'):
            raise MigrationError(f"{self.__class__.__name__} must define 'description' attribute")

    @property
    @abstractmethod
    def version(self) -> int:
        """Migration version number (must be unique and sequential)."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this migration does."""
        pass

    @abstractmethod
    def up(self, client: Neo4jClient) -> None:
        """Apply the migration.

        Args:
            client: Neo4j database client

        Raises:
            MigrationError: If migration fails
        """
        pass

    @abstractmethod
    def down(self, client: Neo4jClient) -> None:
        """Rollback the migration.

        Args:
            client: Neo4j database client

        Raises:
            MigrationError: If rollback fails
        """
        pass

    def validate(self, client: Neo4jClient) -> bool:
        """Validate that the migration can be safely applied.

        Override this method to add custom validation logic.

        Args:
            client: Neo4j database client

        Returns:
            True if migration can be safely applied

        Raises:
            MigrationError: If validation fails with specific reason
        """
        return True

    def get_metadata(self) -> dict:
        """Get migration metadata for tracking.

        Returns:
            Dictionary with version, description, and timestamp
        """
        return {
            "version": self.version,
            "description": self.description,
            "applied_at": datetime.utcnow().isoformat(),
            "migration_class": self.__class__.__name__
        }

    def __str__(self) -> str:
        """String representation of migration."""
        return f"Migration {self.version:03d}: {self.description}"

    def __repr__(self) -> str:
        """Detailed representation of migration."""
        return f"<{self.__class__.__name__}(version={self.version}, description='{self.description}')>"
