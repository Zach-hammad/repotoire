"""Unit tests for IngestionPipeline."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest

from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.parsers import CodeParser
from repotoire.models import FileEntity, Relationship, RelationshipType, NodeType


@pytest.fixture
def mock_graph_client():
    """Create a mock Neo4j client."""
    client = MagicMock()
    client.batch_create_nodes.return_value = {}
    client.batch_create_relationships.return_value = 0
    client.get_stats.return_value = {
        "total_nodes": 10,
        "total_files": 5,
        "total_classes": 3,
        "total_functions": 2,
        "total_relationships": 8
    }
    return client


@pytest.fixture
def temp_repo():
    """Create a temporary repository with sample files."""
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    # Create directory structure
    (temp_path / "src").mkdir()
    (temp_path / "tests").mkdir()
    (temp_path / "__pycache__").mkdir()
    (temp_path / ".git").mkdir()
    (temp_path / "node_modules").mkdir()

    # Create Python files
    (temp_path / "main.py").write_text("# Main file")
    (temp_path / "src" / "utils.py").write_text("# Utils file")
    (temp_path / "tests" / "test_main.py").write_text("# Test file")

    # Create files in ignored directories
    (temp_path / "__pycache__" / "cache.py").write_text("# Cache")
    (temp_path / ".git" / "config").write_text("# Git config")
    (temp_path / "node_modules" / "module.py").write_text("# Node module")

    # Create non-Python files
    (temp_path / "README.md").write_text("# README")
    (temp_path / "config.json").write_text("{}")

    yield temp_path

    # Cleanup
    import shutil
    shutil.rmtree(temp_dir)


class TestInitialization:
    """Test pipeline initialization."""

    def test_pipeline_initialization(self, mock_graph_client, temp_repo):
        """Test pipeline initializes correctly."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline.repo_path == temp_repo.resolve()
        assert pipeline.db == mock_graph_client
        assert "python" in pipeline.parsers
        assert isinstance(pipeline.parsers["python"], CodeParser)

    def test_register_parser(self, mock_graph_client, temp_repo):
        """Test parser registration."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        # Create mock parser
        mock_parser = Mock(spec=CodeParser)

        # Register parser
        pipeline.register_parser("javascript", mock_parser)

        assert "javascript" in pipeline.parsers
        assert pipeline.parsers["javascript"] == mock_parser

    def test_configurable_batch_size(self, mock_graph_client, temp_repo):
        """Test that pipeline respects custom batch size configuration."""
        # Test with custom batch size
        custom_batch_size = 50
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client, batch_size=custom_batch_size)

        assert pipeline.batch_size == custom_batch_size

        # Test with default batch size
        pipeline_default = IngestionPipeline(str(temp_repo), mock_graph_client)
        assert pipeline_default.batch_size == IngestionPipeline.DEFAULT_BATCH_SIZE


class TestFileScanning:
    """Test file scanning functionality."""

    def test_scan_default_pattern(self, mock_graph_client, temp_repo):
        """Test scanning with default pattern (*.py)."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        files = pipeline.scan()

        # Should find 3 Python files (main.py, src/utils.py, tests/test_main.py)
        assert len(files) == 3
        assert any(f.name == "main.py" for f in files)
        assert any(f.name == "utils.py" for f in files)
        assert any(f.name == "test_main.py" for f in files)

    def test_scan_custom_pattern(self, mock_graph_client, temp_repo):
        """Test scanning with custom patterns."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        # Scan for markdown files
        files = pipeline.scan(patterns=["**/*.md"])

        assert len(files) == 1
        assert files[0].name == "README.md"

    def test_scan_multiple_patterns(self, mock_graph_client, temp_repo):
        """Test scanning with multiple patterns."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        # Scan for both .py and .md files
        files = pipeline.scan(patterns=["**/*.py", "**/*.md"])

        assert len(files) == 4  # 3 .py + 1 .md

    def test_scan_filters_ignored_directories(self, mock_graph_client, temp_repo):
        """Test that ignored directories are filtered out."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        files = pipeline.scan()

        # Should not include files from __pycache__, .git, node_modules
        file_parts = [set(f.parts) for f in files]
        ignored = {"__pycache__", ".git", "node_modules", ".venv", "venv", "build", "dist"}

        for parts in file_parts:
            assert not any(ig in parts for ig in ignored)

    def test_scan_empty_directory(self, mock_graph_client):
        """Test scanning an empty directory."""
        temp_dir = tempfile.mkdtemp()

        try:
            pipeline = IngestionPipeline(temp_dir, mock_graph_client)
            files = pipeline.scan()

            assert len(files) == 0
        finally:
            import shutil
            shutil.rmtree(temp_dir)


class TestLanguageDetection:
    """Test language detection from file extensions."""

    def test_detect_python(self, mock_graph_client, temp_repo):
        """Test Python file detection."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline._detect_language(Path("test.py")) == "python"

    def test_detect_javascript(self, mock_graph_client, temp_repo):
        """Test JavaScript file detection."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline._detect_language(Path("test.js")) == "javascript"

    def test_detect_typescript(self, mock_graph_client, temp_repo):
        """Test TypeScript file detection."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline._detect_language(Path("test.ts")) == "typescript"
        assert pipeline._detect_language(Path("component.tsx")) == "typescript"

    def test_detect_java(self, mock_graph_client, temp_repo):
        """Test Java file detection."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline._detect_language(Path("Main.java")) == "java"

    def test_detect_go(self, mock_graph_client, temp_repo):
        """Test Go file detection."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline._detect_language(Path("main.go")) == "go"

    def test_detect_rust(self, mock_graph_client, temp_repo):
        """Test Rust file detection."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline._detect_language(Path("main.rs")) == "rust"

    def test_detect_unknown(self, mock_graph_client, temp_repo):
        """Test unknown file extension."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        assert pipeline._detect_language(Path("file.xyz")) == "unknown"
        assert pipeline._detect_language(Path("README.md")) == "unknown"


class TestParseAndExtract:
    """Test parse and extract functionality."""

    def test_parse_and_extract_success(self, mock_graph_client, temp_repo):
        """Test successful parsing and extraction."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        test_file = temp_repo / "main.py"
        test_file.write_text("""
def hello():
    '''Say hello.'''
    return "Hello"
""")

        entities, relationships = pipeline.parse_and_extract(test_file)

        # Should extract at least file entity and function
        assert len(entities) >= 2
        assert any(e.node_type == NodeType.FILE for e in entities)
        assert any(e.node_type == NodeType.FUNCTION for e in entities)

        # Should have CONTAINS relationships
        assert len(relationships) > 0

    def test_parse_and_extract_unsupported_language(self, mock_graph_client, temp_repo):
        """Test handling of unsupported language."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        # Create a file with unsupported extension
        test_file = temp_repo / "test.xyz"
        test_file.write_text("some content")

        entities, relationships = pipeline.parse_and_extract(test_file)

        # Should return empty lists
        assert entities == []
        assert relationships == []

    def test_parse_and_extract_parser_error(self, mock_graph_client, temp_repo):
        """Test error handling when parser raises exception."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        # Mock parser to raise exception
        mock_parser = Mock(spec=CodeParser)
        mock_parser.process_file.side_effect = Exception("Parse error")
        pipeline.register_parser("python", mock_parser)

        test_file = temp_repo / "main.py"

        # Should handle error gracefully and return empty lists
        entities, relationships = pipeline.parse_and_extract(test_file)

        assert entities == []
        assert relationships == []


class TestLoadToGraph:
    """Test loading data to Neo4j graph."""

    def test_load_to_graph_with_data(self, mock_graph_client, temp_repo):
        """Test loading entities and relationships to graph."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        entities = [
            FileEntity(
                name="test.py",
                qualified_name="test.py",
                file_path="test.py",
                line_start=1,
                line_end=10
            )
        ]
        relationships = [
            Relationship(
                source_id="test.py",
                target_id="os",
                rel_type=RelationshipType.IMPORTS
            )
        ]

        pipeline.load_to_graph(entities, relationships)

        # Verify batch operations called
        mock_graph_client.batch_create_nodes.assert_called_once_with(entities)
        mock_graph_client.batch_create_relationships.assert_called_once_with(relationships)

    def test_load_to_graph_empty_entities(self, mock_graph_client, temp_repo):
        """Test loading with empty entities list."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        pipeline.load_to_graph([], [])

        # Should not call batch operations
        mock_graph_client.batch_create_nodes.assert_not_called()
        mock_graph_client.batch_create_relationships.assert_not_called()

    def test_load_to_graph_no_relationships(self, mock_graph_client, temp_repo):
        """Test loading with no relationships."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        entities = [
            FileEntity(
                name="test.py",
                qualified_name="test.py",
                file_path="test.py",
                line_start=1,
                line_end=10
            )
        ]

        pipeline.load_to_graph(entities, [])

        # Should create nodes but not relationships
        mock_graph_client.batch_create_nodes.assert_called_once()
        mock_graph_client.batch_create_relationships.assert_not_called()

    def test_load_to_graph_error_handling(self, mock_graph_client, temp_repo):
        """Test error handling during graph loading."""
        pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

        # Mock batch_create_nodes to raise exception
        mock_graph_client.batch_create_nodes.side_effect = Exception("Database error")

        entities = [
            FileEntity(
                name="test.py",
                qualified_name="test.py",
                file_path="test.py",
                line_start=1,
                line_end=10
            )
        ]

        # Should handle error gracefully (not crash)
        pipeline.load_to_graph(entities, [])


class TestBatchProcessing:
    """Test batch processing logic."""

    def test_ingest_batches_entities(self, mock_graph_client, temp_repo):
        """Test that ingest batches entities every 100 nodes."""
        # Create many files to trigger batching
        for i in range(15):
            (temp_repo / f"file_{i}.py").write_text(f"# File {i}")

        with patch('repotoire.pipeline.ingestion.GraphSchema'):
            pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)
            pipeline.ingest()

        # Should have called batch operations (batches at 100 entities + final batch)
        # Each file creates at least 1 entity (File node)
        assert mock_graph_client.batch_create_nodes.call_count >= 1

    def test_ingest_loads_remaining_entities(self, mock_graph_client, temp_repo):
        """Test that remaining entities are loaded after loop."""
        # Create a few files (not enough to trigger mid-loop batching)
        for i in range(3):
            (temp_repo / f"file_{i}.py").write_text(f"# File {i}")

        with patch('repotoire.pipeline.ingestion.GraphSchema'):
            pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)
            pipeline.ingest()

        # Should have called batch operations at least once (final batch)
        assert mock_graph_client.batch_create_nodes.call_count >= 1


class TestErrorHandling:
    """Test error handling in the pipeline."""

    def test_ingest_continues_on_parse_error(self, mock_graph_client, temp_repo):
        """Test that ingest continues when a file fails to parse."""
        # Create test files
        (temp_repo / "good.py").write_text("def foo(): pass")
        (temp_repo / "bad.py").write_text("def bad(): pass")

        with patch('repotoire.pipeline.ingestion.GraphSchema'):
            pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

            # Mock parser to fail on bad.py
            original_parse = pipeline.parse_and_extract

            def mock_parse(file_path):
                if "bad.py" in str(file_path):
                    return [], []  # Simulate parse error
                return original_parse(file_path)

            pipeline.parse_and_extract = mock_parse

            # Should complete without raising exception
            pipeline.ingest()

            # Should have processed at least good.py
            assert mock_graph_client.batch_create_nodes.call_count >= 1

    def test_ingest_handles_no_files(self, mock_graph_client):
        """Test ingest handles empty directory gracefully."""
        temp_dir = tempfile.mkdtemp()

        try:
            with patch('repotoire.pipeline.ingestion.GraphSchema'):
                pipeline = IngestionPipeline(temp_dir, mock_graph_client)

                # Should not crash
                pipeline.ingest()

                # Should not have loaded any data
                mock_graph_client.batch_create_nodes.assert_not_called()
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_ingest_initializes_schema(self, mock_graph_client, temp_repo):
        """Test that ingest initializes graph schema."""
        with patch('repotoire.pipeline.ingestion.GraphSchema') as mock_schema_class:
            mock_schema = MagicMock()
            mock_schema_class.return_value = mock_schema

            pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)
            pipeline.ingest()

            # Should have initialized schema
            mock_schema_class.assert_called_once_with(mock_graph_client)
            mock_schema.initialize.assert_called_once()

    def test_ingest_calls_get_stats(self, mock_graph_client, temp_repo):
        """Test that ingest calls get_stats at the end."""
        (temp_repo / "test.py").write_text("# Test")

        with patch('repotoire.pipeline.ingestion.GraphSchema'):
            pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)
            pipeline.ingest()

            # Should have called get_stats
            mock_graph_client.get_stats.assert_called_once()


class TestCustomPatterns:
    """Test custom file patterns."""

    def test_ingest_with_custom_patterns(self, mock_graph_client, temp_repo):
        """Test ingesting with custom file patterns."""
        # Create different file types
        (temp_repo / "test.py").write_text("# Python")
        (temp_repo / "README.md").write_text("# Markdown")

        with patch('repotoire.pipeline.ingestion.GraphSchema'):
            pipeline = IngestionPipeline(str(temp_repo), mock_graph_client)

            # Ingest only markdown files
            pipeline.ingest(patterns=["**/*.md"])

            # Should have processed files
            # Note: markdown has no parser, so will be skipped in parse_and_extract
            # But scan should have found it
            files = pipeline.scan(patterns=["**/*.md"])
            assert len(files) == 1
            assert files[0].name == "README.md"
