"""Integration tests for DeadCodeDetector false positive prevention (REPO-118).

Tests that functions with USES relationships are NOT flagged as dead code.

REPO-367: Uses shared conftest.py fixtures with autouse cleanup for test isolation.
"""

import os
import tempfile
from pathlib import Path

import pytest

from repotoire.parsers.python_parser import PythonParser
from repotoire.detectors.dead_code import DeadCodeDetector
from repotoire.models import NodeType, RelationshipType

# Note: test_neo4j_client fixture is provided by tests/integration/conftest.py
# Graph is automatically cleared before each test by isolate_graph_test autouse fixture


@pytest.fixture
def parser():
    """Create Python parser instance."""
    return PythonParser()


@pytest.fixture
def clean_db(test_neo4j_client):
    """Provide a clean database client for tests.

    Graph clearing is handled automatically by isolate_graph_test autouse fixture.
    """
    yield test_neo4j_client


@pytest.fixture
def sample_python_file():
    """Create a temporary Python file for testing."""
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    yield temp_file
    temp_file.close()
    Path(temp_file.name).unlink()


class TestDeadCodeWithUsesRelationship:
    """Test that functions with USES are not flagged as dead code."""

    def test_function_passed_as_argument_not_dead(self, clean_db, parser, sample_python_file):
        """Function passed as argument should NOT be flagged as dead code."""
        sample_python_file.write("""
def helper_func():
    '''Helper that is passed as argument.'''
    return "helped"

def processor(func):
    '''Processes a function.'''
    return func()

def main():
    '''Main function that passes helper as argument.'''
    return processor(helper_func)
""")
        sample_python_file.flush()

        # Parse and store
        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Verify USES relationship exists
        uses_query = """
        MATCH (source)-[r:USES]->(target)
        RETURN source.name as source, target.name as target
        """
        uses_result = clean_db.execute_query(uses_query)
        assert len(uses_result) > 0, "Expected USES relationship to exist"

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # helper_func should NOT be in findings
        dead_names = [f.title for f in findings]
        assert not any("helper_func" in name for name in dead_names), \
            "helper_func should NOT be flagged as dead code - it has USES relationship"

    def test_function_returned_not_dead(self, clean_db, parser, sample_python_file):
        """Function that is returned should NOT be flagged as dead code."""
        sample_python_file.write("""
def target_func():
    '''Function that will be returned.'''
    return "target"

def get_function():
    '''Returns target_func.'''
    return target_func
""")
        sample_python_file.flush()

        # Parse and store
        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Verify USES relationship exists
        uses_query = """
        MATCH (source)-[r:USES]->(target)
        RETURN source.name as source, target.name as target
        """
        uses_result = clean_db.execute_query(uses_query)
        assert len(uses_result) > 0, "Expected USES relationship to exist"

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # target_func should NOT be in findings
        dead_names = [f.title for f in findings]
        assert not any("target_func" in name for name in dead_names), \
            "target_func should NOT be flagged as dead code - it is returned"

    def test_nested_function_returned_not_dead(self, clean_db, parser, sample_python_file):
        """Nested function that is returned should NOT be flagged as dead code."""
        sample_python_file.write("""
def outer():
    '''Outer function with nested.'''
    def nested():
        '''Nested function that is returned.'''
        return "nested"
    return nested
""")
        sample_python_file.flush()

        # Parse and store
        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Verify USES relationship exists
        uses_query = """
        MATCH (source)-[r:USES]->(target)
        WHERE target.name = 'nested'
        RETURN source.name as source, target.name as target
        """
        uses_result = clean_db.execute_query(uses_query)
        assert len(uses_result) > 0, "Expected USES relationship to nested function"

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # nested should NOT be in findings
        dead_names = [f.title for f in findings]
        assert not any("nested" in name and "Unused function" in name for name in dead_names), \
            "nested function should NOT be flagged as dead code - it is returned"


class TestDeadCodeDecoratorPattern:
    """Test that decorator pattern functions are not flagged as dead code."""

    def test_decorator_wrapper_not_dead(self, clean_db, parser, sample_python_file):
        """Wrapper function in decorator should NOT be flagged as dead code."""
        sample_python_file.write("""
def my_decorator(func):
    '''A decorator.'''
    def wrapper(*args, **kwargs):
        '''Wrapper that is returned.'''
        return func(*args, **kwargs)
    return wrapper

@my_decorator
def decorated():
    '''Decorated function.'''
    return "decorated"
""")
        sample_python_file.flush()

        # Parse and store
        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # wrapper should NOT be in findings
        dead_names = [f.title for f in findings]
        assert not any("wrapper" in name for name in dead_names), \
            "wrapper function should NOT be flagged as dead code - it is returned"

    def test_decorator_factory_inner_functions_not_dead(self, clean_db, parser, sample_python_file):
        """All functions in decorator factory should NOT be flagged as dead code."""
        sample_python_file.write("""
def decorator_factory(param):
    '''Decorator factory.'''
    def actual_decorator(func):
        '''The actual decorator.'''
        def wrapper(*args, **kwargs):
            '''Wrapper function.'''
            return func(*args, **kwargs)
        return wrapper
    return actual_decorator
""")
        sample_python_file.flush()

        # Parse and store
        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # None of the inner functions should be flagged
        dead_names = [f.title for f in findings]
        assert not any("actual_decorator" in name for name in dead_names), \
            "actual_decorator should NOT be flagged - it is returned"
        assert not any("wrapper" in name for name in dead_names), \
            "wrapper should NOT be flagged - it is returned"


class TestDeadCodeQueryVerification:
    """Verify the DeadCodeDetector query structure."""

    def test_query_checks_uses_relationship(self, clean_db):
        """Verify that DeadCodeDetector query checks USES relationships."""
        # The query in DeadCodeDetector should include:
        # AND NOT (f)<-[:USES]-()

        detector = DeadCodeDetector(clean_db)

        # Get the query from _find_dead_functions by checking the source
        import inspect
        source = inspect.getsource(detector._find_dead_functions)

        assert "USES" in source, \
            "DeadCodeDetector._find_dead_functions should check USES relationships"
        assert "NOT (f)<-[:USES]-()" in source or "NOT (f)<-[:USES]-" in source, \
            "DeadCodeDetector should exclude functions with incoming USES relationships"


class TestFixtureIntegration:
    """Test with fixture files to prevent regressions."""

    def test_decorator_patterns_fixture_no_false_positives(self, clean_db, parser):
        """Test that decorator patterns fixture has no false positives."""
        fixture_path = "tests/fixtures/decorator_patterns.py"

        tree = parser.parse(fixture_path)
        entities = parser.extract_entities(tree, fixture_path)
        relationships = parser.extract_relationships(tree, fixture_path, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # Check for common false positive patterns
        dead_names = [f.title for f in findings]

        # Wrapper functions should not be flagged
        wrapper_findings = [n for n in dead_names if "wrapper" in n.lower()]
        assert len(wrapper_findings) == 0, \
            f"Wrapper functions should not be dead code: {wrapper_findings}"

    def test_function_references_fixture_no_false_positives(self, clean_db, parser):
        """Test that function references fixture has no false positives."""
        fixture_path = "tests/fixtures/function_references.py"

        tree = parser.parse(fixture_path)
        entities = parser.extract_entities(tree, fixture_path)
        relationships = parser.extract_relationships(tree, fixture_path, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Verify USES relationships were created
        uses_query = "MATCH ()-[r:USES]->() RETURN count(r) as count"
        uses_count = clean_db.execute_query(uses_query)[0]["count"]
        assert uses_count > 0, "Expected USES relationships from fixture"

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # Functions that are referenced should not be flagged
        dead_names = [f.title for f in findings]

        # helper_function is passed as argument - should not be dead
        assert not any("helper_function" in n for n in dead_names), \
            "helper_function is passed as argument - should not be dead code"
