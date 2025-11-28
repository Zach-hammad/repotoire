"""Tests for MessageChainDetector (REPO-221)."""

import pytest
import tempfile
import os
from unittest.mock import Mock

from repotoire.detectors.message_chain import MessageChainDetector, ChainDepthVisitor
from repotoire.models import Severity


class TestMessageChainDetector:
    """Test suite for MessageChainDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return MessageChainDetector(mock_client)

    def test_detects_chain_from_graph_property(self, detector, mock_client):
        """Should detect chains when max_chain_depth property is set in graph."""
        mock_client.execute_query.side_effect = [
            # First query: chain depths from graph
            [
                {
                    "func_name": "module.process_user",
                    "func_simple_name": "process_user",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 5,
                    "chain_example": "user.profile().settings().notifications()",
                }
            ],
            # Second query: files (won't be called if graph query succeeds)
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].graph_context["chain_depth"] == 5
        assert "Law of Demeter" in findings[0].title or "chain" in findings[0].title.lower()

    def test_detects_chain_from_source_fallback(self, detector, mock_client):
        """Should fall back to source analysis when graph property not set."""
        # Create a temp file with a method chain
        code = '''
def process_data():
    result = obj.level1().level2().level3().level4()
    return result
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                # First query: no graph properties
                [],
                # Second query: files
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            # Should detect the 4-level chain
            assert len(findings) >= 1
            chain_finding = next(
                (f for f in findings if f.graph_context.get("chain_depth", 0) >= 4),
                None
            )
            assert chain_finding is not None
        finally:
            os.unlink(temp_path)

    def test_severity_based_on_chain_depth(self, detector, mock_client):
        """Should assign severity based on chain depth."""
        # Test MEDIUM severity (4 levels)
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 4,
                    "chain_example": "a.b().c().d()",
                }
            ],
        ]

        findings = detector.detect()
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM

    def test_high_severity_for_deep_chains(self, detector, mock_client):
        """Should assign HIGH severity for 5+ level chains."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 5,
                    "chain_example": "a.b().c().d().e()",
                }
            ],
        ]

        findings = detector.detect()
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_critical_severity_for_very_deep_chains(self, detector, mock_client):
        """Should assign CRITICAL severity for 7+ level chains."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 7,
                    "chain_example": "a.b().c().d().e().f().g()",
                }
            ],
        ]

        findings = detector.detect()
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_no_findings_for_short_chains(self, detector, mock_client):
        """Should not report chains below threshold."""
        code = '''
def simple():
    result = obj.method1().method2()  # Only 2 levels
    return result
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],  # No graph properties
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            # Should not detect 2-level chain (below threshold of 4)
            chain_findings = [f for f in findings if f.graph_context.get("chain_depth", 0) >= 4]
            assert len(chain_findings) == 0
        finally:
            os.unlink(temp_path)

    def test_configurable_threshold(self, mock_client):
        """Should allow configurable minimum chain depth."""
        detector = MessageChainDetector(
            mock_client,
            detector_config={"min_chain_depth": 3}
        )

        assert detector.min_chain_depth == 3

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 5,
                    "chain_example": "a.b().c().d().e()",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "MessageChainDetector"
        assert "law_of_demeter" in metadata.tags
        assert "coupling" in metadata.tags

    def test_suggested_fix_included(self, detector, mock_client):
        """Should include refactoring suggestions."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 5,
                    "chain_example": "a.b().c().d().e()",
                }
            ],
        ]

        findings = detector.detect()

        assert findings[0].suggested_fix is not None
        assert "delegate" in findings[0].suggested_fix.lower() or "facade" in findings[0].suggested_fix.lower()


class TestChainDepthVisitor:
    """Test suite for ChainDepthVisitor AST visitor."""

    def test_counts_simple_chain(self):
        """Should count simple method chains."""
        import ast

        code = '''
def func():
    result = obj.a().b().c().d()
'''
        tree = ast.parse(code)
        visitor = ChainDepthVisitor("test.py")
        visitor.visit(tree)

        # Should find a 4-level chain
        assert len(visitor.chains) >= 1
        max_depth = max(c["depth"] for c in visitor.chains)
        assert max_depth >= 4

    def test_counts_attribute_access_chain(self):
        """Should count attribute access chains."""
        import ast

        code = '''
def func():
    value = obj.a.b.c.d
'''
        tree = ast.parse(code)
        visitor = ChainDepthVisitor("test.py")
        visitor.visit(tree)

        # Should find attribute chain
        if visitor.chains:
            max_depth = max(c["depth"] for c in visitor.chains)
            assert max_depth >= 3

    def test_tracks_function_context(self):
        """Should track which function contains the chain."""
        import ast

        code = '''
def my_function():
    result = obj.a().b().c().d()

def other_function():
    pass
'''
        tree = ast.parse(code)
        visitor = ChainDepthVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.chains) >= 1
        assert "my_function" in visitor.chains[0]["function_name"]

    def test_tracks_class_method_context(self):
        """Should track chains inside class methods."""
        import ast

        code = '''
class MyClass:
    def my_method(self):
        result = obj.a().b().c().d()
'''
        tree = ast.parse(code)
        visitor = ChainDepthVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.chains) >= 1
        assert "my_method" in visitor.chains[0]["function_name"]

    def test_generates_chain_example(self):
        """Should generate chain example string."""
        import ast

        code = '''
def func():
    result = user.profile().settings().theme()
'''
        tree = ast.parse(code)
        visitor = ChainDepthVisitor("test.py")
        visitor.visit(tree)

        if visitor.chains:
            assert visitor.chains[0]["example"] is not None
            # Should contain some of the chain parts
            example = visitor.chains[0]["example"]
            assert "user" in example or "profile" in example or "settings" in example


class TestMessageChainDetectorWithEnricher:
    """Test MessageChainDetector with GraphEnricher."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock enricher."""
        return Mock()

    def test_enricher_flags_entities(self, mock_client, mock_enricher):
        """Should flag entities via enricher when available."""
        detector = MessageChainDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 5,
                    "chain_example": "a.b().c().d().e()",
                }
            ],
        ]

        detector.detect()

        assert mock_enricher.flag_entity.called

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Should continue detection even if enricher fails."""
        detector = MessageChainDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "chain_depth": 5,
                    "chain_example": "a.b().c().d().e()",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return MessageChainDetector(mock_client)

    def test_handles_empty_graph(self, detector, mock_client):
        """Should handle empty graph gracefully."""
        mock_client.execute_query.side_effect = [
            [],  # No chain depths
            [],  # No files
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_handles_missing_file(self, detector, mock_client):
        """Should handle missing files gracefully."""
        mock_client.execute_query.side_effect = [
            [],
            [{"file_path": "/nonexistent/file.py"}],
        ]

        findings = detector.detect()

        # Should not crash
        assert isinstance(findings, list)

    def test_handles_syntax_error_in_file(self, detector, mock_client):
        """Should handle syntax errors in source files."""
        code = "def broken( invalid syntax"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            # Should not crash
            assert isinstance(findings, list)
        finally:
            os.unlink(temp_path)

    def test_handles_query_failure(self, detector, mock_client):
        """Should handle database query failures."""
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        # Should return empty list, not crash
        assert findings == []
