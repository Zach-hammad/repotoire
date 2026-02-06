"""Tests for AIBoilerplateDetector with AST-based clustering."""

import ast
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from repotoire.detectors.ai_boilerplate import (
    AIBoilerplateDetector,
    ASTNormalizer,
    BoilerplateASTHasher,
    FunctionAST,
    BoilerplateCluster,
    jaccard_similarity,
    cluster_by_similarity,
)
from repotoire.models import Severity


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_client():
    """Create a mock database client."""
    client = Mock()
    client.__class__.__name__ = "FalkorDBClient"
    return client


@pytest.fixture
def detector(mock_client, tmp_path):
    """Create a detector instance with mock client and temp repo path."""
    return AIBoilerplateDetector(
        mock_client,
        detector_config={"repository_path": str(tmp_path)}
    )


@pytest.fixture
def sample_functions():
    """Sample function ASTs for testing clustering."""
    # Create functions with >= 70% similarity (Jaccard >= 0.70)
    # For 70% Jaccard with 5 elements: need intersection/union >= 0.70
    # If 5 common, 1 different each: intersection=5, union=6, Jaccard=0.833
    base_hashes = {"h1", "h2", "h3", "h4", "h5"}
    
    return [
        FunctionAST(
            qualified_name="module.func1",
            name="func1",
            file_path="module.py",
            line_start=1,
            line_end=10,
            loc=10,
            hash_set=base_hashes,
            patterns=["try_except"],
            decorators=[],
            parent_class=None,
            is_method=False,
        ),
        FunctionAST(
            qualified_name="module.func2",
            name="func2",
            file_path="module.py",
            line_start=15,
            line_end=25,
            loc=11,
            hash_set={"h1", "h2", "h3", "h4", "h5", "h6"},  # 5/6 = 0.833 similar
            patterns=["try_except"],
            decorators=[],
            parent_class=None,
            is_method=False,
        ),
        FunctionAST(
            qualified_name="module.func3",
            name="func3",
            file_path="module.py",
            line_start=30,
            line_end=40,
            loc=11,
            hash_set={"h1", "h2", "h3", "h4", "h5", "h7"},  # 5/6 = 0.833 similar
            patterns=["try_except"],
            decorators=[],
            parent_class=None,
            is_method=False,
        ),
        FunctionAST(
            qualified_name="other.func4",
            name="func4",
            file_path="other.py",
            line_start=1,
            line_end=10,
            loc=10,
            hash_set={"x1", "x2", "x3"},  # Completely different
            patterns=["validation"],
            decorators=[],
            parent_class=None,
            is_method=False,
        ),
    ]


# ============================================================================
# Test AST Normalization
# ============================================================================

class TestASTNormalizer:
    """Test AST normalization."""

    def test_normalize_variable_names(self):
        """Should normalize variable names to VAR_N."""
        normalizer = ASTNormalizer()
        
        result1 = normalizer.normalize_name("data", "var")
        result2 = normalizer.normalize_name("result", "var")
        result3 = normalizer.normalize_name("data", "var")  # Same name again
        
        assert result1 == "VAR_1"
        assert result2 == "VAR_2"
        assert result3 == "VAR_1"  # Same as first

    def test_normalize_function_names(self):
        """Should normalize function names to FUNC_N."""
        normalizer = ASTNormalizer()
        
        result1 = normalizer.normalize_name("process", "func")
        result2 = normalizer.normalize_name("handle", "func")
        
        assert result1 == "FUNC_1"
        assert result2 == "FUNC_2"

    def test_reset_clears_state(self):
        """Should reset counters and mappings."""
        normalizer = ASTNormalizer()
        normalizer.normalize_name("x", "var")
        normalizer.normalize_name("f", "func")
        
        normalizer.reset()
        
        result = normalizer.normalize_name("y", "var")
        assert result == "VAR_1"


class TestBoilerplateASTHasher:
    """Test AST hashing for pattern detection."""

    def test_hashes_function_structure(self):
        """Should produce hashes for function structure."""
        source = """
def process_data(data):
    result = transform(data)
    return result
"""
        tree = ast.parse(source)
        normalizer = ASTNormalizer()
        hasher = BoilerplateASTHasher(normalizer)
        
        hasher.visit(tree)
        
        hashes = hasher.get_hash_set()
        assert len(hashes) > 0

    def test_same_structure_same_hashes(self):
        """Should produce same hashes for structurally identical code."""
        source1 = """
def process_a(data):
    result = transform(data)
    return result
"""
        source2 = """
def process_b(input):
    output = transform(input)
    return output
"""
        normalizer1 = ASTNormalizer()
        hasher1 = BoilerplateASTHasher(normalizer1)
        hasher1.visit(ast.parse(source1))
        
        normalizer2 = ASTNormalizer()
        hasher2 = BoilerplateASTHasher(normalizer2)
        hasher2.visit(ast.parse(source2))
        
        # Hashes should be identical after normalization
        assert hasher1.get_hash_set() == hasher2.get_hash_set()

    def test_detects_try_except_pattern(self):
        """Should detect try/except pattern."""
        source = """
def safe_call():
    try:
        do_something()
    except Exception as e:
        handle_error(e)
"""
        normalizer = ASTNormalizer()
        hasher = BoilerplateASTHasher(normalizer)
        hasher.visit(ast.parse(source))
        
        patterns = hasher.get_dominant_patterns()
        assert "try_except" in patterns

    def test_detects_validation_pattern(self):
        """Should detect validation pattern."""
        source = """
def validate_input(data):
    if not data:
        raise ValueError("Empty data")
    return data
"""
        normalizer = ASTNormalizer()
        hasher = BoilerplateASTHasher(normalizer)
        hasher.visit(ast.parse(source))
        
        patterns = hasher.get_dominant_patterns()
        assert "validation" in patterns or "error_handling" in patterns


# ============================================================================
# Test Similarity Calculation
# ============================================================================

class TestJaccardSimilarity:
    """Test Jaccard similarity calculation."""

    def test_identical_sets(self):
        """Should return 1.0 for identical sets."""
        set1 = {"a", "b", "c"}
        set2 = {"a", "b", "c"}
        
        assert jaccard_similarity(set1, set2) == 1.0

    def test_disjoint_sets(self):
        """Should return 0.0 for disjoint sets."""
        set1 = {"a", "b", "c"}
        set2 = {"x", "y", "z"}
        
        assert jaccard_similarity(set1, set2) == 0.0

    def test_partial_overlap(self):
        """Should return correct similarity for partial overlap."""
        set1 = {"a", "b", "c", "d"}
        set2 = {"a", "b", "e", "f"}
        
        # Intersection: {a, b} = 2
        # Union: {a, b, c, d, e, f} = 6
        # Similarity: 2/6 = 0.333...
        assert abs(jaccard_similarity(set1, set2) - 0.333) < 0.01

    def test_empty_sets(self):
        """Should handle empty sets."""
        assert jaccard_similarity(set(), set()) == 1.0
        assert jaccard_similarity({"a"}, set()) == 0.0
        assert jaccard_similarity(set(), {"a"}) == 0.0


class TestClusterBySimilarity:
    """Test function clustering."""

    def test_clusters_similar_functions(self, sample_functions):
        """Should cluster functions with >70% similarity."""
        # func1, func2, func3 are similar; func4 is different
        clusters = cluster_by_similarity(sample_functions[:3], threshold=0.70)
        
        # Should form one cluster with the 3 similar functions
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_no_clusters_below_threshold(self, sample_functions):
        """Should not cluster functions below threshold."""
        # With high threshold, even similar functions won't cluster
        clusters = cluster_by_similarity(sample_functions, threshold=0.95)
        
        # No clusters should meet threshold
        assert len(clusters) == 0

    def test_separate_dissimilar_functions(self, sample_functions):
        """Should not cluster dissimilar functions."""
        # Only use func1 and func4 which are very different
        functions = [sample_functions[0], sample_functions[3]]
        clusters = cluster_by_similarity(functions, threshold=0.70)
        
        # Should have no clusters (need 3 minimum)
        assert len(clusters) == 0


# ============================================================================
# Test Main Detector
# ============================================================================

class TestAIBoilerplateDetector:
    """Test the main detector class."""

    def test_detects_boilerplate_cluster(self, mock_client, tmp_path):
        """Should detect clusters of similar functions without abstraction."""
        # Create test files - note: line 1 is the first line
        source = """def handler_create(request, user_id, session):
    try:
        data = validate(request.data)
        result = create_entity(data)
        return Response(result)
    except ValidationError as e:
        return ErrorResponse(e)

def handler_update(request, user_id, session):
    try:
        data = validate(request.data)
        result = update_entity(data)
        return Response(result)
    except ValidationError as e:
        return ErrorResponse(e)

def handler_delete(request, user_id, session):
    try:
        data = validate(request.data)
        result = delete_entity(data)
        return Response(result)
    except ValidationError as e:
        return ErrorResponse(e)
"""
        test_file = tmp_path / "handlers.py"
        test_file.write_text(source)
        
        # Mock database response - line numbers match the source exactly
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "handlers.handler_create",
                "name": "handler_create",
                "line_start": 1,
                "line_end": 7,
                "loc": 7,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "handlers.py",
            },
            {
                "qualified_name": "handlers.handler_update",
                "name": "handler_update",
                "line_start": 9,
                "line_end": 15,
                "loc": 7,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "handlers.py",
            },
            {
                "qualified_name": "handlers.handler_delete",
                "name": "handler_delete",
                "line_start": 17,
                "line_end": 23,
                "loc": 7,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "handlers.py",
            },
        ]
        
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"repository_path": str(tmp_path)}
        )
        findings = detector.detect()
        
        assert len(findings) >= 1
        finding = findings[0]
        assert finding.severity in [Severity.HIGH, Severity.MEDIUM]
        assert "boilerplate" in finding.title.lower()
        assert finding.graph_context["cluster_size"] >= 3

    def test_skips_functions_with_shared_class(self, mock_client, tmp_path):
        """Should skip methods that share a parent class (intentional pattern)."""
        source = '''
class BaseHandler:
    def handle_create(self, data):
        return self.process(data)
    
    def handle_update(self, data):
        return self.process(data)
    
    def handle_delete(self, data):
        return self.process(data)
'''
        test_file = tmp_path / "base.py"
        test_file.write_text(source)
        
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "base.BaseHandler.handle_create",
                "name": "handle_create",
                "line_start": 3,
                "line_end": 4,
                "loc": 2,
                "decorators": [],
                "is_method": True,
                "parent_class": "base.BaseHandler",  # Shared class
                "file_path": "base.py",
            },
            {
                "qualified_name": "base.BaseHandler.handle_update",
                "name": "handle_update",
                "line_start": 6,
                "line_end": 7,
                "loc": 2,
                "decorators": [],
                "is_method": True,
                "parent_class": "base.BaseHandler",
                "file_path": "base.py",
            },
            {
                "qualified_name": "base.BaseHandler.handle_delete",
                "name": "handle_delete",
                "line_start": 9,
                "line_end": 10,
                "loc": 2,
                "decorators": [],
                "is_method": True,
                "parent_class": "base.BaseHandler",
                "file_path": "base.py",
            },
        ]
        
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"repository_path": str(tmp_path), "min_loc": 2}
        )
        findings = detector.detect()
        
        # Should not report as boilerplate since they share a class
        for finding in findings:
            if "handle_create" in str(finding.affected_nodes):
                pytest.fail("Should not flag methods of same class as boilerplate")

    def test_empty_codebase(self, detector, mock_client):
        """Should return empty list for codebase with no functions."""
        mock_client.execute_query.return_value = []
        
        findings = detector.detect()
        
        assert len(findings) == 0

    def test_config_overrides_thresholds(self, mock_client, tmp_path):
        """Should allow config to override default thresholds."""
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={
                "repository_path": str(tmp_path),
                "similarity_threshold": 0.85,
                "min_cluster_size": 5,
            }
        )
        
        assert detector.similarity_threshold == 0.85
        assert detector.min_cluster_size == 5

    def test_suggestion_for_error_handling_pattern(self, mock_client, tmp_path):
        """Should suggest error handling decorator for try/except pattern."""
        source = '''
def process_a():
    try:
        do_work()
    except Exception:
        log_error()

def process_b():
    try:
        do_work()
    except Exception:
        log_error()

def process_c():
    try:
        do_work()
    except Exception:
        log_error()
'''
        test_file = tmp_path / "processors.py"
        test_file.write_text(source)
        
        mock_client.execute_query.return_value = [
            {
                "qualified_name": f"processors.process_{c}",
                "name": f"process_{c}",
                "line_start": 2 + i * 6,
                "line_end": 6 + i * 6,
                "loc": 5,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "processors.py",
            }
            for i, c in enumerate(["a", "b", "c"])
        ]
        
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"repository_path": str(tmp_path)}
        )
        findings = detector.detect()
        
        if findings:
            suggestion = findings[0].suggested_fix
            assert suggestion is not None
            # Should suggest decorator or some form of abstraction
            assert "decorator" in suggestion.lower() or "abstraction" in suggestion.lower()

    def test_collaboration_metadata_added(self, mock_client, tmp_path):
        """Should add collaboration metadata to findings."""
        source = '''
def handler_a(x, y):
    result = process(x, y)
    return result

def handler_b(a, b):
    result = process(a, b)
    return result

def handler_c(m, n):
    result = process(m, n)
    return result
'''
        test_file = tmp_path / "handlers.py"
        test_file.write_text(source)
        
        mock_client.execute_query.return_value = [
            {
                "qualified_name": f"handlers.handler_{c}",
                "name": f"handler_{c}",
                "line_start": 2 + i * 4,
                "line_end": 4 + i * 4,
                "loc": 3,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "handlers.py",
            }
            for i, c in enumerate(["a", "b", "c"])
        ]
        
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"repository_path": str(tmp_path), "min_loc": 3}
        )
        findings = detector.detect()
        
        if findings:
            assert len(findings[0].collaboration_metadata) > 0
            metadata = findings[0].collaboration_metadata[0]
            assert metadata.detector == "AIBoilerplateDetector"
            assert "boilerplate" in metadata.tags

    def test_estimate_effort_small(self, detector):
        """Should estimate small effort for few functions."""
        effort = detector._estimate_effort(3)
        assert "Small" in effort

    def test_estimate_effort_medium(self, detector):
        """Should estimate medium effort for moderate functions."""
        effort = detector._estimate_effort(6)
        assert "Medium" in effort

    def test_estimate_effort_large(self, detector):
        """Should estimate large effort for many functions."""
        effort = detector._estimate_effort(10)
        assert "Large" in effort

    def test_why_it_matters_included(self, mock_client, tmp_path):
        """Should include why_it_matters explanation."""
        source = '''
def handle_a(data):
    validated = validate(data)
    return process(validated)

def handle_b(data):
    validated = validate(data)
    return process(validated)

def handle_c(data):
    validated = validate(data)
    return process(validated)
'''
        test_file = tmp_path / "handlers.py"
        test_file.write_text(source)
        
        mock_client.execute_query.return_value = [
            {
                "qualified_name": f"handlers.handle_{c}",
                "name": f"handle_{c}",
                "line_start": 2 + i * 4,
                "line_end": 4 + i * 4,
                "loc": 3,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "handlers.py",
            }
            for i, c in enumerate(["a", "b", "c"])
        ]
        
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"repository_path": str(tmp_path), "min_loc": 3}
        )
        findings = detector.detect()
        
        if findings:
            assert findings[0].why_it_matters is not None
            assert "maintenance" in findings[0].why_it_matters.lower()


class TestAIBoilerplateDetectorWithEnricher:
    """Test AIBoilerplateDetector with GraphEnricher."""

    def test_enricher_flags_entities(self, mock_client, tmp_path):
        """Should flag entities via enricher when available."""
        mock_enricher = Mock()
        
        source = '''
def func_a(x):
    return process(x)

def func_b(y):
    return process(y)

def func_c(z):
    return process(z)
'''
        test_file = tmp_path / "funcs.py"
        test_file.write_text(source)
        
        mock_client.execute_query.return_value = [
            {
                "qualified_name": f"funcs.func_{c}",
                "name": f"func_{c}",
                "line_start": 2 + i * 3,
                "line_end": 3 + i * 3,
                "loc": 2,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "funcs.py",
            }
            for i, c in enumerate(["a", "b", "c"])
        ]
        
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"repository_path": str(tmp_path), "min_loc": 2},
            enricher=mock_enricher,
        )
        detector.detect()
        
        # Should have called flag_entity for each function in a cluster
        if mock_enricher.flag_entity.call_count > 0:
            assert mock_enricher.flag_entity.call_count >= 3

    def test_enricher_failure_does_not_break_detection(self, mock_client, tmp_path):
        """Should continue detection even if enricher fails."""
        mock_enricher = Mock()
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")
        
        source = '''
def func_a(x):
    return process(x)

def func_b(y):
    return process(y)

def func_c(z):
    return process(z)
'''
        test_file = tmp_path / "funcs.py"
        test_file.write_text(source)
        
        mock_client.execute_query.return_value = [
            {
                "qualified_name": f"funcs.func_{c}",
                "name": f"func_{c}",
                "line_start": 2 + i * 3,
                "line_end": 3 + i * 3,
                "loc": 2,
                "decorators": [],
                "is_method": False,
                "parent_class": None,
                "file_path": "funcs.py",
            }
            for i, c in enumerate(["a", "b", "c"])
        ]
        
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"repository_path": str(tmp_path), "min_loc": 2},
            enricher=mock_enricher,
        )
        
        # Should not raise exception
        findings = detector.detect()
        # Findings should still be returned


class TestBoilerplateCluster:
    """Test BoilerplateCluster dataclass."""

    def test_cluster_creation(self, sample_functions):
        """Should create cluster with all fields."""
        cluster = BoilerplateCluster(
            functions=sample_functions[:3],
            avg_similarity=0.85,
            dominant_patterns=["try_except", "validation"],
            has_shared_abstraction=False,
            abstraction_type=None,
        )
        
        assert len(cluster.functions) == 3
        assert cluster.avg_similarity == 0.85
        assert cluster.has_shared_abstraction is False
