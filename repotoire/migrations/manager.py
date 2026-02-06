"""Migration manager for schema versioning and execution."""

import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Dict, List, Optional

from repotoire.graph import FalkorDBClient
from repotoire.logging_config import get_logger
from repotoire.migrations.migration import Migration, MigrationError

logger = get_logger(__name__)


class MigrationManager:
    """Manages database schema migrations and versioning."""

    def __init__(self, client: FalkorDBClient, migrations_dir: Optional[Path] = None):
        """Initialize migration manager.

        Args:
            client: FalkorDB database client
            migrations_dir: Directory containing migration files (default: falkor/migrations)
        """
        self.client = client

        if migrations_dir is None:
            # Default to the migrations directory in the package
            migrations_dir = Path(__file__).parent

        self.migrations_dir = Path(migrations_dir)
        self.migrations: Dict[int, Migration] = {}

        # Initialize schema version tracking
        self._initialize_version_tracking()

        # Load all available migrations
        self._load_migrations()

    def _initialize_version_tracking(self) -> None:
        """Create SchemaVersion node constraint if it doesn't exist."""
        query = """
        CREATE CONSTRAINT schema_version_unique IF NOT EXISTS
        FOR (sv:SchemaVersion)
        REQUIRE sv.version IS UNIQUE
        """
        try:
            self.client.execute_query(query)
            logger.debug("Schema version tracking initialized")
        except Exception as e:
            logger.warning(f"Could not create schema version constraint: {e}")

    def _load_migrations(self) -> None:
        """Discover and load migration files from migrations directory.

        Migration files must:
        - Be named like 001_migration_name.py (3-digit version prefix)
        - Contain a class inheriting from Migration
        - Define version and description properties
        """
        if not self.migrations_dir.exists():
            logger.warning(f"Migrations directory not found: {self.migrations_dir}")
            return

        # Find all migration files
        migration_files = sorted(self.migrations_dir.glob("[0-9][0-9][0-9]_*.py"))

        for file_path in migration_files:
            try:
                # Extract version from filename
                version_str = file_path.stem[:3]
                version = int(version_str)

                # Load the module
                spec = importlib.util.spec_from_file_location(
                    f"falkor.migrations.{file_path.stem}",
                    file_path
                )
                if spec is None or spec.loader is None:
                    logger.warning(f"Could not load migration file: {file_path}")
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find Migration subclass in module
                migration_class = None
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, Migration) and obj is not Migration:
                        migration_class = obj
                        break

                if migration_class is None:
                    logger.warning(f"No Migration subclass found in {file_path}")
                    continue

                # Instantiate migration
                migration = migration_class()

                # Validate version matches filename
                if migration.version != version:
                    raise MigrationError(
                        f"Migration version mismatch in {file_path}: "
                        f"filename has {version}, class has {migration.version}"
                    )

                self.migrations[version] = migration
                logger.debug(f"Loaded migration: {migration}")

            except Exception as e:
                logger.error(f"Failed to load migration {file_path}: {e}")
                raise MigrationError(f"Error loading migration {file_path}: {e}")

        logger.info(f"Loaded {len(self.migrations)} migrations")

    def get_current_version(self) -> int:
        """Get current schema version from database.

        Returns:
            Current version number (0 if no migrations applied)
        """
        query = """
        MATCH (sv:SchemaVersion)
        RETURN sv.version AS version
        ORDER BY sv.version DESC
        LIMIT 1
        """

        try:
            result = self.client.execute_query(query)
            if result and len(result) > 0:
                return result[0]["version"]
            return 0
        except Exception as e:
            logger.warning(f"Could not query schema version: {e}")
            return 0

    def get_migration_history(self) -> List[Dict]:
        """Get all applied migrations from database.

        Returns:
            List of migration records sorted by version
        """
        query = """
        MATCH (sv:SchemaVersion)
        RETURN sv.version AS version,
               sv.description AS description,
               sv.applied_at AS applied_at,
               sv.migration_class AS migration_class
        ORDER BY sv.version ASC
        """

        try:
            return self.client.execute_query(query)
        except Exception as e:
            logger.warning(f"Could not query migration history: {e}")
            return []

    def get_pending_migrations(self) -> List[Migration]:
        """Get migrations that haven't been applied yet.

        Returns:
            List of pending migrations sorted by version
        """
        current_version = self.get_current_version()

        pending = [
            migration
            for version, migration in sorted(self.migrations.items())
            if version > current_version
        ]

        return pending

    def _record_migration(self, migration: Migration) -> None:
        """Record a migration in the database.

        Args:
            migration: Migration that was applied
        """
        metadata = migration.get_metadata()

        query = """
        CREATE (sv:SchemaVersion {
            version: $version,
            description: $description,
            applied_at: $applied_at,
            migration_class: $migration_class
        })
        """

        self.client.execute_query(query, metadata)
        logger.info(f"Recorded migration: {migration}")

    def _remove_migration_record(self, version: int) -> None:
        """Remove a migration record from the database.

        Args:
            version: Version number to remove
        """
        query = """
        MATCH (sv:SchemaVersion {version: $version})
        DELETE sv
        """

        self.client.execute_query(query, {"version": version})
        logger.info(f"Removed migration record for version {version}")

    def migrate(self, target_version: Optional[int] = None) -> None:
        """Apply pending migrations up to target version.

        Args:
            target_version: Target version to migrate to (default: latest)

        Raises:
            MigrationError: If migration fails
        """
        current_version = self.get_current_version()

        # Determine target version
        if target_version is None:
            if not self.migrations:
                logger.info("No migrations available")
                return
            target_version = max(self.migrations.keys())

        if target_version <= current_version:
            logger.info(f"Already at version {current_version}, nothing to do")
            return

        # Get migrations to apply
        to_apply = [
            migration
            for version, migration in sorted(self.migrations.items())
            if current_version < version <= target_version
        ]

        if not to_apply:
            logger.info("No migrations to apply")
            return

        logger.info(f"Applying {len(to_apply)} migrations (v{current_version} -> v{target_version})")

        # Apply each migration
        for migration in to_apply:
            try:
                logger.info(f"Applying: {migration}")

                # Validate migration
                if not migration.validate(self.client):
                    raise MigrationError(f"Validation failed for {migration}")

                # Apply migration
                migration.up(self.client)

                # Record in database
                self._record_migration(migration)

                logger.info(f"Successfully applied: {migration}")

            except Exception as e:
                logger.error(f"Migration failed: {migration}")
                raise MigrationError(
                    f"Failed to apply migration {migration.version}: {e}"
                ) from e

        logger.info(f"Migration complete: now at version {target_version}")

    def rollback(self, target_version: int) -> None:
        """Rollback migrations to target version.

        Args:
            target_version: Version to rollback to

        Raises:
            MigrationError: If rollback fails
        """
        current_version = self.get_current_version()

        if target_version >= current_version:
            logger.info(f"Already at or below version {target_version}, nothing to do")
            return

        # Get migrations to rollback (in reverse order)
        to_rollback = [
            migration
            for version, migration in sorted(self.migrations.items(), reverse=True)
            if target_version < version <= current_version
        ]

        if not to_rollback:
            logger.warning("No migrations to rollback")
            return

        logger.info(f"Rolling back {len(to_rollback)} migrations (v{current_version} -> v{target_version})")

        # Rollback each migration
        for migration in to_rollback:
            try:
                logger.info(f"Rolling back: {migration}")

                # Rollback migration
                migration.down(self.client)

                # Remove record from database
                self._remove_migration_record(migration.version)

                logger.info(f"Successfully rolled back: {migration}")

            except Exception as e:
                logger.error(f"Rollback failed: {migration}")
                raise MigrationError(
                    f"Failed to rollback migration {migration.version}: {e}"
                ) from e

        logger.info(f"Rollback complete: now at version {target_version}")

    def status(self) -> Dict:
        """Get migration status summary.

        Returns:
            Dictionary with current version, pending migrations, and history
        """
        current_version = self.get_current_version()
        pending = self.get_pending_migrations()
        history = self.get_migration_history()

        return {
            "current_version": current_version,
            "available_migrations": len(self.migrations),
            "pending_migrations": len(pending),
            "pending": [
                {
                    "version": m.version,
                    "description": m.description
                }
                for m in pending
            ],
            "history": history
        }
