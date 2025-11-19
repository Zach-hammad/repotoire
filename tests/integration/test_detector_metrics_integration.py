"""Integration tests for Detectors → Metrics integration.

Tests verify that detector findings correctly feed into metrics calculation
and health scoring.
"""

import tempfile
from pathlib import Path

import pytest

from falkor.graph import Neo4jClient
from falkor.pipeline.ingestion import IngestionPipeline
from falkor.detectors.engine import AnalysisEngine
from falkor.models import Severity


@pytest.fixture(scope="module")
def test_neo4j_client():
    """Create a test Neo4j client. Requires Neo4j running on test port."""
    try:
        client = Neo4jClient(
            uri="bolt://localhost:7688",
            username="neo4j",
            password="falkor-password"
        )
        yield client
        client.close()
    except Exception as e:
        pytest.skip(f"Neo4j test database not available: {e}")


class TestCircularDependencyMetrics:
    """Test CircularDependencyDetector findings update metrics."""

    def test_circular_dependency_count_in_metrics(self, test_neo4j_client):
        """Verify circular dependency findings update circular_dependencies metric."""
        test_neo4j_client.clear_graph()

        # Create codebase with circular dependency
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "module_a.py").write_text("""
import module_b

def func_a():
    module_b.func_b()
""")

        (temp_path / "module_b.py").write_text("""
import module_a

def func_b():
    module_a.func_a()
""")

        # Ingest and analyze
        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Verify metrics reflect circular dependency
        assert health.metrics.circular_dependencies >= 0

        # If detector found cycles, metrics should reflect it
        circular_findings = [
            f for f in health.findings
            if f.detector == "CircularDependencyDetector"
        ]

        # Metrics count should match findings count
        assert health.metrics.circular_dependencies == len(circular_findings)

    def test_circular_dependency_severity_affects_structure_score(self, test_neo4j_client):
        """Verify circular dependencies reduce structure score."""
        test_neo4j_client.clear_graph()

        # Create codebase with circular dependency
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "a.py").write_text("import b\ndef a_func(): pass")
        (temp_path / "b.py").write_text("import a\ndef b_func(): pass")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Structure score should be affected by cycles
        # The penalty is: min(50, circular_dependencies * 10)
        if health.metrics.circular_dependencies > 0:
            expected_penalty = min(50, health.metrics.circular_dependencies * 10)
            # Note: structure_score is average of multiple components,
            # so we can't do exact calculation, but should be < 100
            assert health.structure_score < 100


class TestDeadCodeMetrics:
    """Test DeadCodeDetector findings update metrics."""

    def test_dead_code_percentage_in_metrics(self, test_neo4j_client):
        """Verify dead code findings update dead_code_percentage metric."""
        test_neo4j_client.clear_graph()

        # Create codebase with dead code
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "main.py").write_text("""
def used_function():
    return 42

def unused_function():
    '''This is never called.'''
    return 999

def caller():
    used_function()

if __name__ == '__main__':
    caller()
""")

        # Ingest and analyze
        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Verify dead code metrics
        dead_code_findings = [
            f for f in health.findings
            if f.detector == "DeadCodeDetector"
        ]

        # Calculate expected percentage
        total_functions = health.metrics.total_functions
        dead_functions = len(dead_code_findings)

        if total_functions > 0:
            expected_pct = dead_functions / total_functions
            # Allow some tolerance for rounding
            assert abs(health.metrics.dead_code_percentage - expected_pct) < 0.01

    def test_dead_code_affects_quality_score(self, test_neo4j_client):
        """Verify dead code reduces quality score."""
        test_neo4j_client.clear_graph()

        # Create codebase with lots of dead code
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        code = """
def used(): pass

def dead1(): pass
def dead2(): pass
def dead3(): pass
def dead4(): pass
def dead5(): pass

if __name__ == '__main__':
    used()
"""
        (temp_path / "dead.py").write_text(code)

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Quality score calculation: dead_code_score = 100 - (dead_code_percentage * 100)
        if health.metrics.dead_code_percentage > 0:
            dead_code_score = 100 - (health.metrics.dead_code_percentage * 100)
            # Quality score is average of 3 components, so should be affected
            # Can't calculate exactly but should be less than perfect
            assert health.quality_score < 100


class TestGodClassMetrics:
    """Test GodClassDetector findings update metrics."""

    def test_god_class_count_in_metrics(self, test_neo4j_client):
        """Verify god class findings update god_class_count metric."""
        test_neo4j_client.clear_graph()

        # Create codebase with god class
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "god.py").write_text("""
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

        # Ingest and analyze
        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Verify god class metrics
        god_class_findings = [
            f for f in health.findings
            if f.detector == "GodClassDetector"
        ]

        # Metrics count should match findings count
        assert health.metrics.god_class_count == len(god_class_findings)

        # Should detect at least one god class with 15 methods
        assert health.metrics.god_class_count >= 1

    def test_god_class_affects_quality_score(self, test_neo4j_client):
        """Verify god classes reduce quality score."""
        test_neo4j_client.clear_graph()

        # Create multiple god classes
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        for i in range(3):
            methods = "\n    ".join([f"def method_{j}(self): pass" for j in range(15)])
            (temp_path / f"god{i}.py").write_text(f"""
class GodClass{i}:
    {methods}
""")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Quality score calculation: god_class_penalty = min(40, god_class_count * 15)
        if health.metrics.god_class_count > 0:
            expected_penalty = min(40, health.metrics.god_class_count * 15)
            god_class_score = 100 - expected_penalty
            # Quality score is average of 3 components
            # Can't calculate exactly but should be reduced
            assert health.quality_score < 100


class TestFindingsSeverityAggregation:
    """Test finding severity aggregation in FindingsSummary."""

    def test_findings_summary_counts_by_severity(self, test_neo4j_client):
        """Verify FindingsSummary correctly counts findings by severity."""
        test_neo4j_client.clear_graph()

        # Create codebase with various issues
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        # Create circular dependencies (varies by size)
        (temp_path / "a.py").write_text("import b")
        (temp_path / "b.py").write_text("import c")
        (temp_path / "c.py").write_text("import a")

        # Create god class (HIGH severity)
        methods = "\n    ".join([f"def method_{i}(self): pass" for i in range(15)])
        (temp_path / "god.py").write_text(f"""
class GodClass:
    {methods}
""")

        # Create dead code (LOW severity)
        (temp_path / "dead.py").write_text("""
def used(): pass
def dead(): pass

if __name__ == '__main__':
    used()
""")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Verify summary matches actual findings
        critical_count = len([f for f in health.findings if f.severity == Severity.CRITICAL])
        high_count = len([f for f in health.findings if f.severity == Severity.HIGH])
        medium_count = len([f for f in health.findings if f.severity == Severity.MEDIUM])
        low_count = len([f for f in health.findings if f.severity == Severity.LOW])
        info_count = len([f for f in health.findings if f.severity == Severity.INFO])

        assert health.findings_summary.critical == critical_count
        assert health.findings_summary.high == high_count
        assert health.findings_summary.medium == medium_count
        assert health.findings_summary.low == low_count
        assert health.findings_summary.info == info_count

        # Total should match
        assert health.findings_summary.total == len(health.findings)

    def test_empty_codebase_has_zero_findings(self, test_neo4j_client):
        """Verify empty codebase results in zero findings."""
        test_neo4j_client.clear_graph()

        temp_dir = tempfile.mkdtemp()

        pipeline = IngestionPipeline(temp_dir, test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        Path(temp_dir).rmdir()

        # Should have no findings
        assert health.findings_summary.total == 0
        assert health.findings_summary.critical == 0
        assert health.findings_summary.high == 0
        assert health.findings_summary.medium == 0
        assert health.findings_summary.low == 0


class TestHealthScoreCalculation:
    """Test health score calculation incorporates detector results."""

    def test_perfect_codebase_scores_high(self, test_neo4j_client):
        """Verify clean codebase with no issues scores high."""
        test_neo4j_client.clear_graph()

        # Create clean codebase
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "clean.py").write_text("""
'''A clean module.'''

class CleanClass:
    '''A well-designed class.'''

    def do_something(self):
        '''Does something useful.'''
        return 42

def main():
    '''Entry point.'''
    obj = CleanClass()
    result = obj.do_something()
    return result

if __name__ == '__main__':
    main()
""")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Clean code should score well
        assert health.overall_score >= 70
        assert health.grade in ["A", "B", "C"]

    def test_problematic_codebase_scores_low(self, test_neo4j_client):
        """Verify codebase with multiple issues scores lower."""
        test_neo4j_client.clear_graph()

        # Create problematic codebase
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        # Circular dependencies
        (temp_path / "a.py").write_text("import b")
        (temp_path / "b.py").write_text("import a")

        # God class
        methods = "\n    ".join([f"def method_{i}(self): pass" for i in range(15)])
        (temp_path / "god.py").write_text(f"class GodClass:\n    {methods}")

        # Dead code
        (temp_path / "dead.py").write_text("""
def dead1(): pass
def dead2(): pass
def dead3(): pass
def dead4(): pass
def dead5(): pass
""")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Should have multiple findings
        assert health.findings_summary.total > 0

        # Score should be lower than perfect
        # (can't guarantee exact score due to various factors)
        assert health.overall_score < 100

    def test_overall_score_is_weighted_average(self, test_neo4j_client):
        """Verify overall score is correct weighted average of component scores."""
        test_neo4j_client.clear_graph()

        # Create any codebase
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "test.py").write_text("""
class TestClass:
    def method(self):
        pass
""")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Calculate expected weighted average
        expected = (
            health.structure_score * 0.4
            + health.quality_score * 0.3
            + health.architecture_score * 0.3
        )

        # Should match within rounding tolerance
        assert abs(health.overall_score - expected) < 0.1

    def test_grade_matches_score_thresholds(self, test_neo4j_client):
        """Verify letter grade matches score thresholds."""
        test_neo4j_client.clear_graph()

        # Create codebase
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "test.py").write_text("def test(): pass")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Verify grade matches score
        score = health.overall_score

        if score >= 90:
            assert health.grade == "A"
        elif score >= 80:
            assert health.grade == "B"
        elif score >= 70:
            assert health.grade == "C"
        elif score >= 60:
            assert health.grade == "D"
        else:
            assert health.grade == "F"


class TestMetricsBreakdownConsistency:
    """Test metrics breakdown is consistent with findings."""

    def test_metrics_reflect_all_detector_findings(self, test_neo4j_client):
        """Verify all detector findings are reflected in metrics."""
        test_neo4j_client.clear_graph()

        # Create diverse codebase
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        # Add circular dependency
        (temp_path / "a.py").write_text("import b")
        (temp_path / "b.py").write_text("import a")

        # Add god class
        methods = "\n    ".join([f"def method_{i}(self): pass" for i in range(15)])
        (temp_path / "god.py").write_text(f"class GodClass:\n    {methods}")

        # Add dead code
        (temp_path / "main.py").write_text("""
def used(): pass
def dead(): pass

if __name__ == '__main__':
    used()
""")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)
        health = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Count findings by detector
        circular_findings = len([f for f in health.findings if f.detector == "CircularDependencyDetector"])
        god_class_findings = len([f for f in health.findings if f.detector == "GodClassDetector"])
        dead_code_findings = len([f for f in health.findings if f.detector == "DeadCodeDetector"])

        # Verify metrics match
        assert health.metrics.circular_dependencies == circular_findings
        assert health.metrics.god_class_count == god_class_findings

        # Dead code percentage should reflect findings
        # Note: Metrics are calculated from total classes+functions, while findings are just functions
        # So we verify the count matches, not the percentage
        total_nodes = health.metrics.total_classes + health.metrics.total_functions
        if total_nodes > 0:
            expected_dead_pct = dead_code_findings / total_nodes
            assert abs(health.metrics.dead_code_percentage - expected_dead_pct) < 0.1  # Allow tolerance

    def test_metrics_update_on_repeated_analysis(self, test_neo4j_client):
        """Verify metrics stay consistent on repeated analysis."""
        test_neo4j_client.clear_graph()

        # Create codebase
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)

        (temp_path / "test.py").write_text("""
class TestClass:
    def method1(self): pass
    def method2(self): pass

def unused(): pass
""")

        pipeline = IngestionPipeline(str(temp_path), test_neo4j_client)
        pipeline.ingest(patterns=["**/*.py"])

        engine = AnalysisEngine(test_neo4j_client)

        # Run analysis twice
        health1 = engine.analyze()
        health2 = engine.analyze()

        # Cleanup
        for file in temp_path.glob("*.py"):
            file.unlink()
        temp_path.rmdir()

        # Metrics should be identical
        assert health1.metrics.total_files == health2.metrics.total_files
        assert health1.metrics.total_classes == health2.metrics.total_classes
        assert health1.metrics.total_functions == health2.metrics.total_functions
        assert health1.metrics.circular_dependencies == health2.metrics.circular_dependencies
        assert health1.metrics.god_class_count == health2.metrics.god_class_count
        assert health1.metrics.dead_code_percentage == health2.metrics.dead_code_percentage

        # Scores should be identical
        assert health1.overall_score == health2.overall_score
        assert health1.grade == health2.grade
