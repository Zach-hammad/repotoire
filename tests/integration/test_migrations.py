"""Integration tests for schema migration system."""

import pytest
from unittest.mock import MagicMock

from repotoire.migrations import Migration, MigrationManager, MigrationError
from repotoire.graph import FalkorDBClient


class TestMigration001(Migration):
    """Test migration for unit tests."""

    @property
    def version(self) -> int:
        return 1

    @property
    def description(self) -> str:
        return "Test migration for unit tests"

    def up(self, client: FalkorDBClient) -> None:
        """Create test constraint."""
        client.execute_query(
            "CREATE CONSTRAINT test_constraint IF NOT EXISTS FOR (n:TestNode) REQUIRE n.id IS UNIQUE"
        )

    def down(self, client: FalkorDBClient) -> None:
        """Drop test constraint."""
        # Check if constraint exists first
        result = client.execute_query("SHOW CONSTRAINTS YIELD name WHERE name = 'test_constraint' RETURN name")
        if result:
            client.execute_query("DROP CONSTRAINT test_constraint")


@pytest.fixture
def mock_graph_client():
    """Create a mock Neo4j client."""
    client = MagicMock(spec=FalkorDBClient)
    client.execute_query.return_value = []
    return client


class TestMigrationBase:
    """Test Migration base class."""

    def test_migration_requires_version(self, mock_graph_client):
        """Test that migrations must define version."""
        class BadMigration(Migration):
            @property
            def description(self) -> str:
                return "Test"

            def up(self, client: FalkorDBClient) -> None:
                pass

            def down(self, client: FalkorDBClient) -> None:
                pass

        # Abstract properties are enforced by ABC, raising TypeError
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BadMigration()

    def test_migration_requires_description(self, mock_graph_client):
        """Test that migrations must define description."""
        class BadMigration(Migration):
            @property
            def version(self) -> int:
                return 1

            def up(self, client: FalkorDBClient) -> None:
                pass

            def down(self, client: FalkorDBClient) -> None:
                pass

        # Abstract properties are enforced by ABC, raising TypeError
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BadMigration()

    def test_migration_default_validate(self, mock_graph_client):
        """Test default validation returns True."""
        migration = TestMigration001()
        assert migration.validate(mock_graph_client) is True

    def test_migration_get_metadata(self):
        """Test migration metadata."""
        migration = TestMigration001()
        metadata = migration.get_metadata()

        assert metadata["version"] == 1
        assert metadata["description"] == "Test migration for unit tests"
        assert "applied_at" in metadata
        assert metadata["migration_class"] == "TestMigration001"

    def test_migration_str_repr(self):
        """Test migration string representations."""
        migration = TestMigration001()

        assert str(migration) == "Migration 001: Test migration for unit tests"
        assert "TestMigration001" in repr(migration)
        assert "version=1" in repr(migration)


class TestMigrationManager:
    """Test MigrationManager."""

    def test_manager_initialization(self, mock_graph_client):
        """Test manager initializes schema version tracking."""
        manager = MigrationManager(mock_graph_client)

        # Should create version constraint
        calls = mock_graph_client.execute_query.call_args_list
        assert any("SchemaVersion" in str(call) for call in calls)

    def test_get_current_version_empty_db(self, mock_graph_client):
        """Test getting version from empty database."""
        mock_graph_client.execute_query.return_value = []

        manager = MigrationManager(mock_graph_client)
        version = manager.get_current_version()

        assert version == 0

    def test_get_current_version_with_migrations(self, mock_graph_client):
        """Test getting version with applied migrations."""
        mock_graph_client.execute_query.return_value = [{"version": 3}]

        manager = MigrationManager(mock_graph_client)
        version = manager.get_current_version()

        assert version == 3

    def test_get_migration_history(self, mock_graph_client):
        """Test getting migration history."""
        mock_graph_client.execute_query.return_value = [
            {
                "version": 1,
                "description": "Initial schema",
                "applied_at": "2025-01-01T00:00:00",
                "migration_class": "InitialSchemaMigration"
            },
            {
                "version": 2,
                "description": "Add clue nodes",
                "applied_at": "2025-01-02T00:00:00",
                "migration_class": "AddClueNodesMigration"
            }
        ]

        manager = MigrationManager(mock_graph_client)
        history = manager.get_migration_history()

        assert len(history) == 2
        assert history[0]["version"] == 1
        assert history[1]["version"] == 2

    def test_record_migration(self, mock_graph_client):
        """Test recording migration to database."""
        manager = MigrationManager(mock_graph_client)
        migration = TestMigration001()

        manager._record_migration(migration)

        # Should have executed CREATE query for SchemaVersion
        calls = [str(call) for call in mock_graph_client.execute_query.call_args_list]
        assert any("CREATE" in call and "SchemaVersion" in call for call in calls)

    def test_remove_migration_record(self, mock_graph_client):
        """Test removing migration record."""
        manager = MigrationManager(mock_graph_client)

        manager._remove_migration_record(1)

        # Should have executed DELETE query
        calls = [str(call) for call in mock_graph_client.execute_query.call_args_list]
        assert any("DELETE" in call and "SchemaVersion" in call for call in calls)

    def test_status_summary(self, mock_graph_client):
        """Test status summary."""
        # No migrations applied, but have pending migrations
        mock_graph_client.execute_query.return_value = []

        manager = MigrationManager(mock_graph_client)
        manager.migrations = {1: TestMigration001()}

        status = manager.status()

        assert status["current_version"] == 0
        assert status["available_migrations"] == 1
        assert status["pending_migrations"] == 1
        assert len(status["pending"]) == 1
        assert status["pending"][0]["version"] == 1

    def test_migrate_no_migrations_available(self, mock_graph_client):
        """Test migrate with no migrations."""
        mock_graph_client.execute_query.return_value = []

        manager = MigrationManager(mock_graph_client)
        manager.migrations = {}

        # Should not raise error, just log
        manager.migrate()

    def test_migrate_already_at_target(self, mock_graph_client):
        """Test migrate when already at target version."""
        mock_graph_client.execute_query.return_value = [{"version": 1}]

        manager = MigrationManager(mock_graph_client)
        manager.migrations = {1: TestMigration001()}

        # Should not raise error
        manager.migrate(target_version=1)

    def test_migrate_validation_failure(self, mock_graph_client):
        """Test migrate fails if validation fails."""
        mock_graph_client.execute_query.return_value = []

        class FailingMigration(Migration):
            @property
            def version(self) -> int:
                return 1

            @property
            def description(self) -> str:
                return "Failing migration"

            def validate(self, client: FalkorDBClient) -> bool:
                return False

            def up(self, client: FalkorDBClient) -> None:
                pass

            def down(self, client: FalkorDBClient) -> None:
                pass

        manager = MigrationManager(mock_graph_client)
        manager.migrations = {1: FailingMigration()}

        with pytest.raises(MigrationError, match="Validation failed"):
            manager.migrate()

    def test_rollback_no_migrations_to_rollback(self, mock_graph_client):
        """Test rollback when already at target."""
        mock_graph_client.execute_query.return_value = [{"version": 1}]

        manager = MigrationManager(mock_graph_client)
        manager.migrations = {1: TestMigration001()}

        # Should not raise error
        manager.rollback(target_version=1)


class TestMigrationIntegration:
    """Integration tests with mock database operations."""

    def test_full_migration_cycle(self, mock_graph_client):
        """Test applying and rolling back migrations."""
        # Start with no migrations applied
        version_responses = [
            [],  # Initial get_current_version in __init__
            [],  # get_current_version before migrate
            [{"version": 1}],  # get_current_version after migrate
            [{"version": 0}],  # get_current_version after rollback
        ]
        mock_graph_client.execute_query.side_effect = lambda query, *args, **kwargs: (
            version_responses.pop(0) if "MATCH (sv:SchemaVersion)" in query and "RETURN sv.version" in query
            else []
        )

        manager = MigrationManager(mock_graph_client)
        manager.migrations = {1: TestMigration001()}

        # Apply migration
        manager.migrate()

        # Rollback migration
        manager.rollback(target_version=0)
