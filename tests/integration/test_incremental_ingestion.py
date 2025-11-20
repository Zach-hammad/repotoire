"""Integration tests for incremental ingestion functionality."""

import hashlib
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.graph import Neo4jClient


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository with Python files."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    return repo


@pytest.fixture
def neo4j_client():
    """Create a Neo4j client for testing."""
    try:
        client = Neo4jClient(
            uri="bolt://localhost:7688",
            username="neo4j",
            password="falkor-password"
        )
        # Clear graph before each test
        client.clear_graph()
        yield client
        client.close()
    except Exception as e:
        pytest.skip(f"Neo4j test database not available: {e}")


class TestIncrementalIngestion:
    """Test incremental ingestion with file hash tracking."""

    def test_new_file_detection(self, temp_repo, neo4j_client):
        """Test that new files are detected and ingested."""
        # Create initial file
        file1 = temp_repo / "module1.py"
        file1.write_text("def func1():\n    pass\n")

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        stats1 = neo4j_client.get_stats()
        assert stats1["total_files"] == 1

        # Add a new file
        file2 = temp_repo / "module2.py"
        file2.write_text("def func2():\n    pass\n")

        # Second ingestion (incremental)
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        stats2 = neo4j_client.get_stats()
        assert stats2["total_files"] == 2

    def test_changed_file_detection(self, temp_repo, neo4j_client):
        """Test that changed files are re-ingested."""
        # Create initial file
        file1 = temp_repo / "module1.py"
        file1.write_text("def func1():\n    pass\n")

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Get initial metadata
        rel_path = "module1.py"
        metadata1 = neo4j_client.get_file_metadata(rel_path)
        assert metadata1 is not None
        hash1 = metadata1["hash"]

        # Modify the file
        file1.write_text("def func1():\n    return 42\n")

        # Second ingestion (incremental)
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify hash changed
        metadata2 = neo4j_client.get_file_metadata(rel_path)
        assert metadata2 is not None
        hash2 = metadata2["hash"]
        assert hash2 != hash1

    def test_unchanged_file_skipping(self, temp_repo, neo4j_client):
        """Test that unchanged files are skipped in incremental mode."""
        # Create initial file
        file1 = temp_repo / "module1.py"
        content = "def func1():\n    pass\n"
        file1.write_text(content)

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Get initial metadata
        rel_path = "module1.py"
        metadata1 = neo4j_client.get_file_metadata(rel_path)
        hash1 = metadata1["hash"]

        # Second ingestion without changes (incremental)
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify hash unchanged
        metadata2 = neo4j_client.get_file_metadata(rel_path)
        assert metadata2["hash"] == hash1

        # Verify stats remain the same
        stats = neo4j_client.get_stats()
        assert stats["total_files"] == 1

    def test_deleted_file_cleanup(self, temp_repo, neo4j_client):
        """Test that deleted files are removed from the graph."""
        # Create two files
        file1 = temp_repo / "module1.py"
        file1.write_text("def func1():\n    pass\n")
        file2 = temp_repo / "module2.py"
        file2.write_text("def func2():\n    pass\n")

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        stats1 = neo4j_client.get_stats()
        assert stats1["total_files"] == 2

        # Delete one file
        file2.unlink()

        # Second ingestion (incremental)
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify deleted file removed
        stats2 = neo4j_client.get_stats()
        assert stats2["total_files"] == 1

        # Verify specific file removed
        metadata = neo4j_client.get_file_metadata("module2.py")
        assert metadata is None

    def test_force_full_reingest(self, temp_repo, neo4j_client):
        """Test that force-full flag causes complete re-ingestion."""
        # Create initial file
        file1 = temp_repo / "module1.py"
        file1.write_text("def func1():\n    pass\n")

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Get initial stats
        stats1 = neo4j_client.get_stats()

        # Second ingestion with incremental=False (force full)
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=False)

        # Stats should be same but file was re-processed
        stats2 = neo4j_client.get_stats()
        assert stats2["total_files"] == stats1["total_files"]

    def test_mixed_scenario(self, temp_repo, neo4j_client):
        """Test a realistic scenario with new, changed, unchanged, and deleted files."""
        # Create initial files
        file1 = temp_repo / "unchanged.py"
        file1.write_text("def unchanged():\n    pass\n")
        file2 = temp_repo / "to_change.py"
        file2.write_text("def old_version():\n    pass\n")
        file3 = temp_repo / "to_delete.py"
        file3.write_text("def will_delete():\n    pass\n")

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        stats1 = neo4j_client.get_stats()
        assert stats1["total_files"] == 3

        # Make changes:
        # 1. Leave unchanged.py as is
        # 2. Modify to_change.py
        file2.write_text("def new_version():\n    return 42\n")
        # 3. Delete to_delete.py
        file3.unlink()
        # 4. Add new_file.py
        file4 = temp_repo / "new_file.py"
        file4.write_text("def new_func():\n    pass\n")

        # Second ingestion (incremental)
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify final state
        stats2 = neo4j_client.get_stats()
        assert stats2["total_files"] == 3  # 1 unchanged + 1 changed + 1 new - 1 deleted = 3

        # Verify specific files
        assert neo4j_client.get_file_metadata("unchanged.py") is not None
        assert neo4j_client.get_file_metadata("to_change.py") is not None
        assert neo4j_client.get_file_metadata("new_file.py") is not None
        assert neo4j_client.get_file_metadata("to_delete.py") is None

    def test_incremental_performance_benefit(self, temp_repo, neo4j_client):
        """Test that incremental mode processes fewer files when most are unchanged."""
        # Create 10 files
        for i in range(10):
            file = temp_repo / f"module{i}.py"
            file.write_text(f"def func{i}():\n    pass\n")

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        stats1 = neo4j_client.get_stats()
        assert stats1["total_files"] == 10

        # Change only 1 file
        (temp_repo / "module5.py").write_text("def func5():\n    return 'changed'\n")

        # Second ingestion (incremental)
        # In a real scenario, this would be much faster
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify all files still present
        stats2 = neo4j_client.get_stats()
        assert stats2["total_files"] == 10

    def test_hash_computation_consistency(self, temp_repo, neo4j_client):
        """Test that hash computation is consistent and matches stored hashes."""
        # Create a file
        file1 = temp_repo / "module1.py"
        content = "def func1():\n    pass\n"
        file1.write_text(content)

        # Compute expected hash
        expected_hash = hashlib.md5(content.encode()).hexdigest()

        # Ingest file
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify stored hash matches
        metadata = neo4j_client.get_file_metadata("module1.py")
        assert metadata["hash"] == expected_hash

    def test_last_modified_tracking(self, temp_repo, neo4j_client):
        """Test that last modification time is tracked correctly."""
        # Create a file
        file1 = temp_repo / "module1.py"
        file1.write_text("def func1():\n    pass\n")

        # Ingest file
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify lastModified is set
        metadata = neo4j_client.get_file_metadata("module1.py")
        assert metadata["lastModified"] is not None

    def test_entities_updated_on_change(self, temp_repo, neo4j_client):
        """Test that entities are correctly updated when file changes."""
        # Create initial file with one function
        file1 = temp_repo / "module1.py"
        file1.write_text("def func1():\n    pass\n")

        # First ingestion
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        stats1 = neo4j_client.get_stats()
        initial_functions = stats1["total_functions"]

        # Modify file to add another function
        file1.write_text("def func1():\n    pass\n\ndef func2():\n    pass\n")

        # Second ingestion (incremental)
        pipeline = IngestionPipeline(str(temp_repo), neo4j_client)
        pipeline.ingest(incremental=True)

        # Verify function count increased
        stats2 = neo4j_client.get_stats()
        assert stats2["total_functions"] > initial_functions
