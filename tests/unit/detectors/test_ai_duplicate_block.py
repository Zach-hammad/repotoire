"""Tests for AIDuplicateBlockDetector (AST-based similarity)."""

import ast
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from repotoire.detectors.ai_duplicate_block import (
    AIDuplicateBlockDetector,
    ASTNormalizer,
    PythonASTHasher,
    compute_ast_hashes,
    extract_function_source,
    jaccard_similarity,
    parse_function_ast,
    GENERIC_IDENTIFIERS,
)
from repotoire.models import Severity


class TestASTNormalizer:
    """Test AST identifier normalization."""

    def test_normalizes_variables_consistently(self):
        """Test that same variable gets same placeholder."""
        normalizer = ASTNormalizer()
        
        first = normalizer.normalize_name("data", "var")
        second = normalizer.normalize_name("data", "var")
        
        assert first == second
        assert first == "VAR_1"

    def test_different_variables_get_different_placeholders(self):
        """Test that different variables get different placeholders."""
        normalizer = ASTNormalizer()
        
        first = normalizer.normalize_name("data", "var")
        second = normalizer.normalize_name("result", "var")
        
        assert first != second
        assert first == "VAR_1"
        assert second == "VAR_2"

    def test_normalizes_functions(self):
        """Test function name normalization."""
        normalizer = ASTNormalizer()
        
        result = normalizer.normalize_name("process_data", "func")
        
        assert result == "FUNC_1"

    def test_normalizes_classes(self):
        """Test class name normalization."""
        normalizer = ASTNormalizer()
        
        result = normalizer.normalize_name("DataProcessor", "class")
        
        assert result == "CLASS_1"

    def test_reset_clears_state(self):
        """Test that reset clears counters and mappings."""
        normalizer = ASTNormalizer()
        normalizer.normalize_name("data", "var")
        normalizer.normalize_name("process", "func")
        
        normalizer.reset()
        
        # After reset, should start fresh
        result = normalizer.normalize_name("other", "var")
        assert result == "VAR_1"


class TestPythonASTHasher:
    """Test Python AST hashing."""

    def test_hashes_simple_function(self):
        """Test hashing a simple function."""
        source = """
def add(a, b):
    return a + b
"""
        tree = ast.parse(source)
        func_node = tree.body[0]
        
        normalizer = ASTNormalizer()
        hasher = PythonASTHasher(normalizer)
        hasher.visit(func_node)
        
        assert len(hasher.get_hash_set()) > 0

    def test_tracks_identifiers(self):
        """Test that identifiers are tracked."""
        source = """
def process(data, result):
    temp = data + 1
    return temp
"""
        tree = ast.parse(source)
        func_node = tree.body[0]
        
        normalizer = ASTNormalizer()
        hasher = PythonASTHasher(normalizer)
        hasher.visit(func_node)
        
        assert "data" in hasher.identifiers
        assert "result" in hasher.identifiers
        assert "temp" in hasher.identifiers

    def test_generic_name_ratio_calculation(self):
        """Test generic name ratio calculation."""
        # Function with all generic names
        source = """
def func(data, result, temp):
    value = data + temp
    return result
"""
        tree = ast.parse(source)
        func_node = tree.body[0]
        
        normalizer = ASTNormalizer()
        hasher = PythonASTHasher(normalizer)
        hasher.visit(func_node)
        
        ratio = hasher.get_generic_name_ratio()
        # data, result, temp, value, func are all generic
        assert ratio > 0.5

    def test_similar_functions_have_similar_hashes(self):
        """Test that similar functions produce similar hash sets."""
        source1 = """
def process_a(x, y):
    result = x + y
    return result
"""
        source2 = """
def process_b(a, b):
    output = a + b
    return output
"""
        tree1 = ast.parse(source1)
        tree2 = ast.parse(source2)
        
        normalizer1 = ASTNormalizer()
        hasher1 = PythonASTHasher(normalizer1)
        hasher1.visit(tree1.body[0])
        
        normalizer2 = ASTNormalizer()
        hasher2 = PythonASTHasher(normalizer2)
        hasher2.visit(tree2.body[0])
        
        similarity = jaccard_similarity(hasher1.get_hash_set(), hasher2.get_hash_set())
        assert similarity > 0.7  # Should be highly similar


class TestJaccardSimilarity:
    """Test Jaccard similarity calculation."""

    def test_identical_sets(self):
        """Test identical sets have similarity 1.0."""
        set1 = {"a", "b", "c"}
        set2 = {"a", "b", "c"}
        
        assert jaccard_similarity(set1, set2) == 1.0

    def test_completely_different_sets(self):
        """Test completely different sets have similarity 0.0."""
        set1 = {"a", "b", "c"}
        set2 = {"d", "e", "f"}
        
        assert jaccard_similarity(set1, set2) == 0.0

    def test_partial_overlap(self):
        """Test partial overlap gives expected similarity."""
        set1 = {"a", "b", "c", "d"}
        set2 = {"c", "d", "e", "f"}
        
        # Intersection: {c, d} = 2
        # Union: {a, b, c, d, e, f} = 6
        # Jaccard = 2/6 = 0.333...
        similarity = jaccard_similarity(set1, set2)
        assert abs(similarity - 1/3) < 0.01

    def test_empty_sets(self):
        """Test empty sets."""
        assert jaccard_similarity(set(), set()) == 1.0
        assert jaccard_similarity({"a"}, set()) == 0.0
        assert jaccard_similarity(set(), {"a"}) == 0.0


class TestFunctionSourceExtraction:
    """Test function source extraction."""

    def test_extracts_correct_lines(self):
        """Test extraction of correct line range."""
        source = """line 1
line 2
line 3
line 4
line 5
"""
        extracted = extract_function_source(source, 2, 4)
        
        assert "line 2" in extracted
        assert "line 3" in extracted
        assert "line 4" in extracted
        assert "line 1" not in extracted
        assert "line 5" not in extracted

    def test_handles_edge_cases(self):
        """Test edge cases in extraction."""
        source = "single line"
        
        extracted = extract_function_source(source, 1, 1)
        assert extracted == "single line"


class TestParseFunctionAST:
    """Test function AST parsing."""

    def test_parses_simple_function(self):
        """Test parsing a simple function."""
        source = """def add(a, b):
    return a + b
"""
        result = parse_function_ast(source)
        
        assert result is not None
        assert isinstance(result, ast.FunctionDef)
        assert result.name == "add"

    def test_parses_indented_method(self):
        """Test parsing an indented method."""
        source = """    def process(self, data):
        return data * 2
"""
        result = parse_function_ast(source)
        
        assert result is not None
        assert isinstance(result, ast.FunctionDef)

    def test_returns_none_for_invalid_syntax(self):
        """Test that invalid syntax returns None."""
        source = "def broken("
        
        result = parse_function_ast(source)
        
        assert result is None


class TestComputeASTHashes:
    """Test the compute_ast_hashes function."""

    def test_returns_hash_set_and_ratio(self):
        """Test that function returns expected tuple."""
        source = """
def example(data):
    result = data + 1
    return result
"""
        tree = ast.parse(source)
        func_node = tree.body[0]
        
        hash_set, generic_ratio, identifiers = compute_ast_hashes(func_node)
        
        assert isinstance(hash_set, set)
        assert len(hash_set) > 0
        assert 0.0 <= generic_ratio <= 1.0
        assert isinstance(identifiers, list)


class TestGenericIdentifiers:
    """Test generic identifier detection."""

    def test_common_generics_are_detected(self):
        """Test that common generic names are in the set."""
        expected_generics = [
            "result", "data", "temp", "value", "item",
            "obj", "res", "ret", "tmp", "val"
        ]
        for name in expected_generics:
            assert name in GENERIC_IDENTIFIERS

    def test_specific_names_not_generic(self):
        """Test that specific names are not considered generic."""
        specific_names = [
            "user_id", "email_address", "transaction_amount",
            "customer_name", "order_total"
        ]
        for name in specific_names:
            assert name.lower() not in GENERIC_IDENTIFIERS


class TestAIDuplicateBlockDetector:
    """Test the main detector class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_config_overrides_thresholds(self, mock_client, temp_repo):
        """Test config can override default thresholds."""
        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={
                "repository_path": str(temp_repo),
                "similarity_threshold": 0.80,
                "generic_name_threshold": 0.50,
                "min_loc": 10,
                "max_findings": 25,
            }
        )

        assert detector.similarity_threshold == 0.80
        assert detector.generic_name_threshold == 0.50
        assert detector.min_loc == 10
        assert detector.max_findings == 25

    def test_empty_results_return_no_findings(self, mock_client, temp_repo):
        """Test empty query results return no findings."""
        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_query_error_returns_empty(self, mock_client, temp_repo):
        """Test query error returns empty findings list."""
        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        assert len(findings) == 0

    def test_detects_duplicate_functions(self, mock_client, temp_repo):
        """Test detection of near-duplicate functions."""
        # Create test files with similar functions
        file1 = temp_repo / "module_a.py"
        file1.write_text("""
def process_data(data, config):
    result = data + config
    temp = result * 2
    return temp
""")
        
        file2 = temp_repo / "module_b.py"
        file2.write_text("""
def handle_input(input, options):
    output = input + options
    value = output * 2
    return value
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module_a.py::process_data",
                "name": "process_data",
                "line_start": 2,
                "line_end": 6,
                "loc": 5,
                "file_path": "module_a.py",
            },
            {
                "qualified_name": "module_b.py::handle_input",
                "name": "handle_input",
                "line_start": 2,
                "line_end": 6,
                "loc": 5,
                "file_path": "module_b.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        findings = detector.detect()

        assert len(findings) == 1
        assert "process_data" in findings[0].title
        assert "handle_input" in findings[0].title
        assert findings[0].severity in [Severity.HIGH, Severity.MEDIUM, Severity.CRITICAL]

    def test_ignores_same_file_duplicates(self, mock_client, temp_repo):
        """Test that functions in the same file are not flagged."""
        file1 = temp_repo / "module.py"
        file1.write_text("""
def func_a(x, y):
    return x + y

def func_b(a, b):
    return a + b
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.py::func_a",
                "name": "func_a",
                "line_start": 2,
                "line_end": 3,
                "loc": 2,
                "file_path": "module.py",
            },
            {
                "qualified_name": "module.py::func_b",
                "name": "func_b",
                "line_start": 5,
                "line_end": 6,
                "loc": 2,
                "file_path": "module.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        findings = detector.detect()

        # Same file functions should be ignored
        assert len(findings) == 0

    def test_ignores_dissimilar_functions(self, mock_client, temp_repo):
        """Test that dissimilar functions are not flagged."""
        file1 = temp_repo / "module_a.py"
        file1.write_text("""
def simple():
    return 1
""")
        
        file2 = temp_repo / "module_b.py"
        file2.write_text("""
def complex_function(a, b, c, d, e):
    result = []
    for i in range(a):
        for j in range(b):
            if i > j:
                result.append(i * j)
            else:
                result.append(c + d + e)
    return result
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module_a.py::simple",
                "name": "simple",
                "line_start": 2,
                "line_end": 3,
                "loc": 2,
                "file_path": "module_a.py",
            },
            {
                "qualified_name": "module_b.py::complex_function",
                "name": "complex_function",
                "line_start": 2,
                "line_end": 11,
                "loc": 10,
                "file_path": "module_b.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        findings = detector.detect()

        assert len(findings) == 0

    def test_collaboration_metadata_added(self, mock_client, temp_repo):
        """Test collaboration metadata is added to findings."""
        file1 = temp_repo / "a.py"
        file1.write_text("""
def process(data):
    result = data * 2
    return result
""")
        
        file2 = temp_repo / "b.py"
        file2.write_text("""
def handle(input):
    output = input * 2
    return output
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::process",
                "name": "process",
                "line_start": 2,
                "line_end": 5,
                "loc": 4,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::handle",
                "name": "handle",
                "line_start": 2,
                "line_end": 5,
                "loc": 4,
                "file_path": "b.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        findings = detector.detect()

        if findings:
            assert len(findings[0].collaboration_metadata) > 0
            metadata = findings[0].collaboration_metadata[0]
            assert metadata.detector == "AIDuplicateBlockDetector"
            assert "ai_duplicate" in metadata.tags

    def test_graph_context_includes_ast_similarity(self, mock_client, temp_repo):
        """Test graph context includes AST similarity score."""
        file1 = temp_repo / "x.py"
        file1.write_text("""
def func1(a, b):
    c = a + b
    return c
""")
        
        file2 = temp_repo / "y.py"
        file2.write_text("""
def func2(x, y):
    z = x + y
    return z
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "x.py::func1",
                "name": "func1",
                "line_start": 2,
                "line_end": 5,
                "loc": 4,
                "file_path": "x.py",
            },
            {
                "qualified_name": "y.py::func2",
                "name": "func2",
                "line_start": 2,
                "line_end": 5,
                "loc": 4,
                "file_path": "y.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        findings = detector.detect()

        if findings:
            assert "ast_similarity" in findings[0].graph_context
            assert findings[0].graph_context["ast_similarity"] >= 0.70


class TestAIDuplicateBlockDetectorGenericNaming:
    """Test generic naming detection."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        return Mock()

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_detects_generic_naming_pattern(self, mock_client, temp_repo):
        """Test detection of generic naming patterns."""
        file1 = temp_repo / "a.py"
        file1.write_text("""
def process(data, result, temp, value):
    item = data + result
    obj = temp * value
    return item + obj
""")
        
        file2 = temp_repo / "b.py"
        file2.write_text("""
def handle(input, output, tmp, val):
    elem = input + output
    res = tmp * val
    return elem + res
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::process",
                "name": "process",
                "line_start": 2,
                "line_end": 6,
                "loc": 5,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::handle",
                "name": "handle",
                "line_start": 2,
                "line_end": 6,
                "loc": 5,
                "file_path": "b.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )
        findings = detector.detect()

        if findings:
            # Should detect generic naming
            assert findings[0].graph_context.get("has_generic_naming", False) or \
                   findings[0].graph_context.get("func1_generic_ratio", 0) > 0.3


class TestAIDuplicateBlockDetectorWithEnricher:
    """Test AIDuplicateBlockDetector with GraphEnricher."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        return Mock()

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock enricher."""
        return Mock()

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_enricher_flags_entities(self, mock_client, mock_enricher, temp_repo):
        """Test entities are flagged via enricher."""
        file1 = temp_repo / "a.py"
        file1.write_text("""
def func_a(x):
    y = x * 2
    return y
""")
        
        file2 = temp_repo / "b.py"
        file2.write_text("""
def func_b(a):
    b = a * 2
    return b
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::func_a",
                "name": "func_a",
                "line_start": 2,
                "line_end": 5,
                "loc": 4,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::func_b",
                "name": "func_b",
                "line_start": 2,
                "line_end": 5,
                "loc": 4,
                "file_path": "b.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)},
            enricher=mock_enricher,
        )
        findings = detector.detect()

        if findings:
            # Should flag both entities
            assert mock_enricher.flag_entity.call_count >= 2

    def test_enricher_failure_does_not_break_detection(
        self, mock_client, mock_enricher, temp_repo
    ):
        """Test detection continues even if enricher fails."""
        file1 = temp_repo / "a.py"
        file1.write_text("""
def f1(x):
    return x + 1
""")
        
        file2 = temp_repo / "b.py"
        file2.write_text("""
def f2(y):
    return y + 1
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::f1",
                "name": "f1",
                "line_start": 2,
                "line_end": 3,
                "loc": 2,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::f2",
                "name": "f2",
                "line_start": 2,
                "line_end": 3,
                "loc": 2,
                "file_path": "b.py",
            },
        ]

        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)},
            enricher=mock_enricher,
        )

        # Should not raise exception
        findings = detector.detect()
        assert isinstance(findings, list)


class TestAIDuplicateBlockDetectorEdgeCases:
    """Test edge cases for AIDuplicateBlockDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        return Mock()

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_handles_missing_file(self, mock_client, temp_repo):
        """Test handling of missing files."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "nonexistent.py::func",
                "name": "func",
                "line_start": 1,
                "line_end": 5,
                "loc": 5,
                "file_path": "nonexistent.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Should not raise
        findings = detector.detect()
        assert isinstance(findings, list)

    def test_handles_unparseable_code(self, mock_client, temp_repo):
        """Test handling of unparseable code."""
        file1 = temp_repo / "broken.py"
        file1.write_text("def broken(")  # Invalid syntax

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "broken.py::broken",
                "name": "broken",
                "line_start": 1,
                "line_end": 1,
                "loc": 1,
                "file_path": "broken.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Should not raise
        findings = detector.detect()
        assert isinstance(findings, list)

    def test_respects_max_findings_limit(self, mock_client, temp_repo):
        """Test that max_findings limit is respected."""
        # Create many similar files
        for i in range(10):
            f = temp_repo / f"module{i}.py"
            f.write_text(f"""
def func{i}(x):
    result = x * 2
    return result
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": f"module{i}.py::func{i}",
                "name": f"func{i}",
                "line_start": 2,
                "line_end": 5,
                "loc": 4,
                "file_path": f"module{i}.py",
            }
            for i in range(10)
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={
                "repository_path": str(temp_repo),
                "max_findings": 3,
            }
        )

        findings = detector.detect()
        assert len(findings) <= 3

    def test_handles_empty_functions(self, mock_client, temp_repo):
        """Test handling of empty/pass-only functions."""
        file1 = temp_repo / "empty.py"
        file1.write_text("""
def empty1():
    pass

def empty2():
    pass
""")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "empty.py::empty1",
                "name": "empty1",
                "line_start": 2,
                "line_end": 3,
                "loc": 2,
                "file_path": "empty.py",
            },
        ]

        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Should not raise
        findings = detector.detect()
        assert isinstance(findings, list)
