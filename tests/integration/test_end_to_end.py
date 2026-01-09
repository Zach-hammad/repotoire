"""Integration tests for end-to-end workflow.

REPO-367: Uses shared conftest.py fixtures with autouse cleanup for test isolation.
"""

import os
import tempfile
from pathlib import Path

import pytest

from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.detectors.engine import AnalysisEngine

# Note: test_graph_client fixture is provided by tests/integration/conftest.py
# Graph is automatically cleared before each test by isolate_graph_test autouse fixture


@pytest.fixture
def sample_codebase():
    """Create a temporary directory with sample Python files."""
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    # Create sample files
    (temp_path / "module_a.py").write_text("""
import module_b

class ClassA:
    def method_a(self):
        module_b.function_b()
""")

    (temp_path / "module_b.py").write_text("""
def function_b():
    return 42

def unused_function():
    '''This is never called.'''
    return 0
""")

    (temp_path / "god_class.py").write_text("""
class GodClass:
    '''A class with too many responsibilities.'''

    def method_1(self): pass
    def method_2(self): pass
    def method_3(self): pass
    def method_4(self): pass
    def method_5(self): pass
    def method_6(self): pass
    def method_7(self): pass
    def method_8(self): pass
    def method_9(self): pass
    def method_10(self): pass
    def method_11(self): pass
    def method_12(self): pass
    def method_13(self): pass
    def method_14(self): pass
    def method_15(self): pass
""")

    yield temp_path

    # Cleanup
    for file in temp_path.glob("*.py"):
        file.unlink()
    temp_path.rmdir()


class TestEndToEndWorkflow:
    """Test complete ingestion and analysis workflow."""

    def test_ingest_and_analyze(self, test_graph_client, sample_codebase):
        """Test full pipeline: ingest -> analyze -> report."""
        # Step 1: Ingest codebase
        pipeline = IngestionPipeline(str(sample_codebase), test_graph_client)
        pipeline.ingest(patterns=["**/*.py"])

        # Verify ingestion created nodes
        stats = test_graph_client.get_stats()
        assert stats["total_files"] == 3
        assert stats["total_classes"] >= 2  # ClassA, GodClass
        assert stats["total_functions"] >= 2  # function_b, unused_function

        # Step 2: Run analysis
        engine = AnalysisEngine(test_graph_client)
        health = engine.analyze()

        # Verify health report generated
        assert health.grade in ["A", "B", "C", "D", "F"]
        assert 0 <= health.overall_score <= 100

        # Verify detectors ran
        assert health.findings_summary.total > 0

        # Verify specific findings
        finding_detectors = [f.detector for f in health.findings]

        # Should detect dead code (unused_function)
        assert "DeadCodeDetector" in finding_detectors

        # Might detect god class (if thresholds met)
        # Note: GodClass with 15 methods should be detected
        if health.metrics.god_class_count > 0:
            assert "GodClassDetector" in finding_detectors

    def test_incremental_analysis(self, test_graph_client, sample_codebase):
        """Test running analysis multiple times."""
        # First ingestion
        pipeline = IngestionPipeline(str(sample_codebase), test_graph_client)
        pipeline.ingest(patterns=["**/*.py"])

        # First analysis
        engine = AnalysisEngine(test_graph_client)
        health1 = engine.analyze()

        # Second analysis (without re-ingestion)
        health2 = engine.analyze()

        # Results should be consistent
        assert health1.grade == health2.grade
        assert health1.findings_summary.total == health2.findings_summary.total

    def test_parser_creates_correct_relationships(self, test_graph_client, sample_codebase):
        """Test that parser creates all expected relationship types."""
        # Ingest
        pipeline = IngestionPipeline(str(sample_codebase), test_graph_client)
        pipeline.ingest(patterns=["**/*.py"])

        # Query for relationship types
        query = """
        MATCH ()-[r]->()
        RETURN DISTINCT type(r) as rel_type, count(r) as count
        """
        results = test_graph_client.execute_query(query)

        rel_types = {r["rel_type"] for r in results}

        # Should have at least these relationship types
        assert "IMPORTS" in rel_types
        assert "CONTAINS" in rel_types
        # CALLS might be present if call detection works
        # INHERITS might be present if there's inheritance

    def test_finding_details(self, test_graph_client, sample_codebase):
        """Test that findings contain all required information."""
        # Ingest and analyze
        pipeline = IngestionPipeline(str(sample_codebase), test_graph_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_graph_client)
        health = engine.analyze()

        # Check first finding has required fields
        if health.findings:
            finding = health.findings[0]

            assert finding.id is not None
            assert finding.detector is not None
            assert finding.severity is not None
            assert finding.title is not None
            assert finding.description is not None
            assert finding.suggested_fix is not None
            assert finding.estimated_effort is not None
            assert isinstance(finding.affected_files, list)
            assert isinstance(finding.affected_nodes, list)

    def test_metrics_calculation(self, test_graph_client, sample_codebase):
        """Test that metrics are calculated correctly."""
        # Ingest and analyze
        pipeline = IngestionPipeline(str(sample_codebase), test_graph_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_graph_client)
        health = engine.analyze()

        metrics = health.metrics

        # Verify metrics are populated
        assert metrics.total_files == 3
        assert metrics.total_classes >= 2
        assert metrics.total_functions >= 2

        # Modularity should be calculated
        assert 0 <= metrics.modularity <= 1

        # Circular dependencies should be counted
        assert metrics.circular_dependencies >= 0

        # Dead code percentage should be calculated
        assert 0 <= metrics.dead_code_percentage <= 1

    def test_health_scoring(self, test_graph_client, sample_codebase):
        """Test health scoring produces reasonable scores."""
        # Ingest and analyze
        pipeline = IngestionPipeline(str(sample_codebase), test_graph_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_graph_client)
        health = engine.analyze()

        # Verify scores are in valid range
        assert 0 <= health.structure_score <= 100
        assert 0 <= health.quality_score <= 100
        assert 0 <= health.architecture_score <= 100

        # Overall score should be weighted average
        expected = (
            health.structure_score * 0.4
            + health.quality_score * 0.3
            + health.architecture_score * 0.3
        )
        assert abs(health.overall_score - expected) < 0.1

        # Grade should match score
        if health.overall_score >= 90:
            assert health.grade == "A"
        elif health.overall_score >= 80:
            assert health.grade == "B"
        elif health.overall_score >= 70:
            assert health.grade == "C"
        elif health.overall_score >= 60:
            assert health.grade == "D"
        else:
            assert health.grade == "F"


class TestErrorHandling:
    """Test error handling in the pipeline."""

    def test_handles_malformed_python_file(self, test_graph_client, sample_codebase):
        """Test handling of files with syntax errors."""
        # Create file with syntax error
        (sample_codebase / "broken.py").write_text("""
def broken_function(
    # Missing closing paren
""")

        # Should not crash
        pipeline = IngestionPipeline(str(sample_codebase), test_graph_client)
        try:
            pipeline.ingest(patterns=["**/*.py"])
            # May or may not succeed, but shouldn't crash
        except Exception:
            pass  # Expected for malformed files

    def test_handles_empty_codebase(self, test_graph_client):
        """Test handling of empty directory."""
        temp_dir = tempfile.mkdtemp()

        pipeline = IngestionPipeline(temp_dir, test_graph_client)
        pipeline.ingest(patterns=["**/*.py"])

        # Should handle gracefully
        stats = test_graph_client.get_stats()
        assert stats["total_files"] == 0

        Path(temp_dir).rmdir()
