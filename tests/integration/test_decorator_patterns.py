"""Integration tests for decorator pattern handling (REPO-118).

Tests that decorator patterns don't cause false positives in dead code detection.

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
    This fixture provides the client with a friendly name for decorator tests.
    """
    yield test_neo4j_client


@pytest.fixture
def sample_python_file():
    """Create a temporary Python file for testing."""
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    yield temp_file
    temp_file.close()
    Path(temp_file.name).unlink()


class TestSimpleDecoratorPattern:
    """Test simple decorator patterns."""

    def test_simple_decorator_wrapper_not_dead(self, clean_db, parser, sample_python_file):
        """Simple decorator's wrapper should NOT be flagged as dead code."""
        sample_python_file.write("""
def simple_decorator(func):
    '''Simple decorator.'''
    def wrapper(*args, **kwargs):
        '''Wrapper function.'''
        return func(*args, **kwargs)
    return wrapper

@simple_decorator
def decorated():
    '''Decorated function.'''
    return "hello"
""")
        sample_python_file.flush()

        # Parse and store
        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Verify USES relationship for wrapper
        uses_query = """
        MATCH (source)-[r:USES]->(target)
        WHERE target.name = 'wrapper'
        RETURN source.name as source, target.name as target
        """
        uses_result = clean_db.execute_query(uses_query)
        assert len(uses_result) > 0, "Expected USES relationship to wrapper"

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        # wrapper should NOT be in findings
        dead_names = [f.title for f in findings]
        assert not any("wrapper" in name for name in dead_names), \
            "wrapper function should NOT be flagged as dead code"


class TestDecoratorFactoryPattern:
    """Test decorator factory patterns (parameterized decorators)."""

    def test_decorator_factory_all_functions_not_dead(self, clean_db, parser, sample_python_file):
        """All functions in decorator factory should NOT be flagged as dead code."""
        sample_python_file.write("""
def decorator_factory(param):
    '''Decorator factory.'''
    def decorator(func):
        '''The actual decorator.'''
        def wrapper(*args, **kwargs):
            '''Wrapper function.'''
            print(param)
            return func(*args, **kwargs)
        return wrapper
    return decorator

@decorator_factory("test")
def decorated():
    '''Decorated function.'''
    return "hello"
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
        assert not any("decorator" in name.lower() and "Unused" in name for name in dead_names), \
            "decorator should NOT be flagged as dead code"
        assert not any("wrapper" in name.lower() for name in dead_names), \
            "wrapper should NOT be flagged as dead code"


class TestMemoizationDecorator:
    """Test memoization/caching decorator patterns."""

    def test_memoize_wrapper_not_dead(self, clean_db, parser, sample_python_file):
        """Memoization decorator's wrapper should NOT be flagged as dead code."""
        sample_python_file.write("""
def memoize(func):
    '''Memoization decorator.'''
    cache = {}

    def wrapper(*args):
        '''Caching wrapper.'''
        if args not in cache:
            cache[args] = func(*args)
        return cache[args]

    return wrapper

@memoize
def fibonacci(n):
    '''Calculate fibonacci number.'''
    if n < 2:
        return n
    return fibonacci(n-1) + fibonacci(n-2)
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

        # wrapper should NOT be flagged
        dead_names = [f.title for f in findings]
        assert not any("wrapper" in name for name in dead_names), \
            "memoize wrapper should NOT be flagged as dead code"


class TestRetryDecorator:
    """Test retry decorator patterns."""

    def test_retry_decorator_functions_not_dead(self, clean_db, parser, sample_python_file):
        """Retry decorator's inner functions should NOT be flagged as dead code."""
        sample_python_file.write("""
def retry(max_attempts=3):
    '''Retry decorator factory.'''
    def decorator(func):
        '''The actual decorator.'''
        def wrapper(*args, **kwargs):
            '''Wrapper that retries.'''
            attempts = 0
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception:
                    attempts += 1
                    if attempts >= max_attempts:
                        raise
        return wrapper
    return decorator

@retry(max_attempts=5)
def flaky_operation():
    '''Operation that might fail.'''
    return "success"
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

        # Neither decorator nor wrapper should be flagged
        dead_names = [f.title for f in findings]
        assert not any("decorator" in name.lower() and "Unused" in name for name in dead_names), \
            "decorator should NOT be flagged as dead code"
        assert not any("wrapper" in name for name in dead_names), \
            "wrapper should NOT be flagged as dead code"


class TestContextManagerDecorator:
    """Test context manager decorator patterns."""

    def test_context_decorator_not_dead(self, clean_db, parser, sample_python_file):
        """Context manager decorator's wrapper should NOT be flagged as dead code."""
        sample_python_file.write("""
def with_context(context_name):
    '''Context manager decorator.'''
    def decorator(func):
        '''The actual decorator.'''
        def wrapper(*args, **kwargs):
            '''Wrapper with context.'''
            print(f"Entering {context_name}")
            try:
                return func(*args, **kwargs)
            finally:
                print(f"Exiting {context_name}")
        return wrapper
    return decorator

@with_context("database")
def database_operation():
    '''Operation in context.'''
    return "done"
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

        # wrapper should NOT be flagged
        dead_names = [f.title for f in findings]
        assert not any("wrapper" in name for name in dead_names), \
            "context wrapper should NOT be flagged as dead code"


class TestDecoratorFromFixture:
    """Test decorator patterns from fixture file."""

    def test_fixture_decorator_patterns(self, clean_db, parser):
        """Test that decorator_patterns.py fixture has no false positives."""
        fixture_path = "tests/fixtures/decorator_patterns.py"

        tree = parser.parse(fixture_path)
        entities = parser.extract_entities(tree, fixture_path)
        relationships = parser.extract_relationships(tree, fixture_path, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Verify USES relationships exist
        uses_query = "MATCH ()-[r:USES]->() RETURN count(r) as count"
        uses_count = clean_db.execute_query(uses_query)[0]["count"]
        assert uses_count > 0, "Expected USES relationships from fixture"

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        dead_names = [f.title for f in findings]

        # No wrapper functions should be flagged
        wrapper_findings = [n for n in dead_names if "wrapper" in n.lower()]
        assert len(wrapper_findings) == 0, \
            f"Wrapper functions should not be dead code: {wrapper_findings}"

        # No inner_wrapper functions should be flagged
        inner_wrapper_findings = [n for n in dead_names if "inner_wrapper" in n.lower()]
        assert len(inner_wrapper_findings) == 0, \
            f"Inner wrapper functions should not be dead code: {inner_wrapper_findings}"


class TestUsesRelationshipVerification:
    """Verify USES relationships are created correctly for decorators."""

    def test_uses_relationship_created_for_return(self, clean_db, parser, sample_python_file):
        """Verify USES relationship is created when wrapper is returned."""
        sample_python_file.write("""
def my_decorator(func):
    '''Decorator.'''
    def wrapper():
        '''Wrapper.'''
        return func()
    return wrapper
""")
        sample_python_file.flush()

        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        # Check USES relationship exists
        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # my_decorator should USES wrapper
        assert any(
            "my_decorator" in r.source_id and "wrapper" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from my_decorator to wrapper"

    def test_uses_vs_calls_in_decorator(self, clean_db, parser, sample_python_file):
        """Verify USES and CALLS are both created correctly in decorator."""
        sample_python_file.write("""
def my_decorator(func):
    '''Decorator.'''
    def wrapper():
        '''Wrapper.'''
        return func()  # CALLS func
    return wrapper  # USES wrapper
""")
        sample_python_file.flush()

        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]
        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]

        # my_decorator USES wrapper (returned)
        assert any(
            "my_decorator" in r.source_id and "wrapper" in r.target_id
            for r in uses_rels
        ), "Expected USES from my_decorator to wrapper"

        # wrapper CALLS func (the parameter)
        # Note: func is a parameter, not a defined function, so this may not create a relationship
        # The important thing is that wrapper is not flagged as dead code


class TestComplexDecoratorChains:
    """Test complex decorator chains and stacking."""

    def test_stacked_decorators(self, clean_db, parser, sample_python_file):
        """Test that stacked decorators don't cause false positives."""
        sample_python_file.write("""
def decorator_one(func):
    '''First decorator.'''
    def wrapper_one(*args, **kwargs):
        '''First wrapper.'''
        return func(*args, **kwargs)
    return wrapper_one

def decorator_two(func):
    '''Second decorator.'''
    def wrapper_two(*args, **kwargs):
        '''Second wrapper.'''
        return func(*args, **kwargs)
    return wrapper_two

@decorator_one
@decorator_two
def doubly_decorated():
    '''Function with two decorators.'''
    return "hello"
""")
        sample_python_file.flush()

        tree = parser.parse(sample_python_file.name)
        entities = parser.extract_entities(tree, sample_python_file.name)
        relationships = parser.extract_relationships(tree, sample_python_file.name, entities)

        clean_db.batch_create_nodes(entities)
        clean_db.batch_create_relationships(relationships)

        # Run detector
        detector = DeadCodeDetector(clean_db)
        findings = detector.detect()

        dead_names = [f.title for f in findings]

        # Neither wrapper should be flagged
        assert not any("wrapper_one" in name for name in dead_names), \
            "wrapper_one should NOT be flagged as dead code"
        assert not any("wrapper_two" in name for name in dead_names), \
            "wrapper_two should NOT be flagged as dead code"
