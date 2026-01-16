"""Integration tests for DeadCodeDetector decorator queries (type mismatch fix).

Tests that the FalkorDB queries work correctly with decorator fields,
specifically testing for the "Type mismatch: expected Boolean but was String" error.
"""

import os
import pytest

from repotoire.models import ClassEntity, FunctionEntity, NodeType


@pytest.fixture
def clean_db():
    """Create a FalkorDB client for testing."""
    from repotoire.graph import FalkorDBClient

    # Try to connect to local FalkorDB
    host = os.getenv("REPOTOIRE_FALKORDB_HOST", "localhost")
    port = int(os.getenv("REPOTOIRE_FALKORDB_PORT", "6381"))  # Local dev port
    graph_name = "repotoire_decorator_test"

    try:
        client = FalkorDBClient(host=host, port=port, graph_name=graph_name)
        # Clear the test graph
        client.clear_graph()
        yield client
        # Cleanup after test
        client.clear_graph()
        client.close()
    except Exception as e:
        pytest.skip(f"FalkorDB not available: {e}")


class TestDecoratorQueries:
    """Test FalkorDB queries with decorator fields."""

    def test_size_with_null_decorators(self, clean_db):
        """Test size() function when decorators is NULL."""
        # Create a class without decorators
        class_entity = ClassEntity(
            name="TestClassNoDecorators",
            qualified_name="test_module.TestClassNoDecorators",
            file_path="/test/module.py",
            line_start=1,
            line_end=10,
            docstring="Test class",
            node_type=NodeType.CLASS,
            decorators=[],  # Empty list
            is_abstract=False,
            complexity=1,
        )

        clean_db.batch_create_nodes([class_entity])

        # Test query with size() - this should NOT fail
        query = """
        MATCH (c:Class)
        WHERE c.qualifiedName = $qn
        RETURN c.decorators AS decorators, size(COALESCE(c.decorators, [])) AS dec_size
        """
        results = clean_db.execute_query(query, {"qn": "test_module.TestClassNoDecorators"})

        assert len(results) == 1
        record = results[0]
        # Decorators might be null or empty list
        assert record["dec_size"] == 0 or record["decorators"] is None

    def test_size_with_populated_decorators(self, clean_db):
        """Test size() function when decorators is populated."""
        # Create a class with decorators
        class_entity = ClassEntity(
            name="TestClassWithDecorators",
            qualified_name="test_module.TestClassWithDecorators",
            file_path="/test/module.py",
            line_start=1,
            line_end=10,
            docstring="Test class",
            node_type=NodeType.CLASS,
            decorators=["dataclass", "frozen"],
            is_abstract=False,
            complexity=1,
        )

        clean_db.batch_create_nodes([class_entity])

        # Test query with size()
        query = """
        MATCH (c:Class)
        WHERE c.qualifiedName = $qn
        RETURN c.decorators AS decorators, size(COALESCE(c.decorators, [])) AS dec_size
        """
        results = clean_db.execute_query(query, {"qn": "test_module.TestClassWithDecorators"})

        assert len(results) == 1
        record = results[0]
        assert record["dec_size"] == 2
        assert "dataclass" in record["decorators"]

    def test_dead_class_query_pattern(self, clean_db):
        """Test the exact query pattern from DeadCodeDetector._find_dead_classes."""
        # Create test classes - one with decorators, one without
        classes = [
            ClassEntity(
                name="DecoratedClass",
                qualified_name="test_module.DecoratedClass",
                file_path="/test/module.py",
                line_start=1,
                line_end=10,
                docstring="Decorated class",
                node_type=NodeType.CLASS,
                decorators=["dataclass"],
                is_abstract=False,
                complexity=1,
            ),
            ClassEntity(
                name="PlainClass",
                qualified_name="test_module.PlainClass",
                file_path="/test/module.py",
                line_start=20,
                line_end=30,
                docstring="Plain class",
                node_type=NodeType.CLASS,
                decorators=[],
                is_abstract=False,
                complexity=1,
            ),
        ]

        clean_db.batch_create_nodes(classes)

        # Run the actual query pattern from DeadCodeDetector (without decorator filter)
        query = """
        MATCH (c:Class)
        WHERE NOT (c)<-[:CALLS]-()
          AND NOT (c)<-[:INHERITS]-()
          AND NOT (c)<-[:USES]-()
        RETURN c.qualifiedName AS qualified_name,
               c.name AS name,
               c.decorators AS decorators
        """

        results = clean_db.execute_query(query)

        # Should return both classes
        assert len(results) == 2

        # Verify we can filter decorators in Python
        for record in results:
            decorators = record.get("decorators")
            name = record["name"]

            if name == "DecoratedClass":
                assert decorators and len(decorators) > 0
            else:
                # PlainClass should have empty or null decorators
                assert not decorators or len(decorators) == 0

    def test_problematic_size_query(self, clean_db):
        """Test the problematic size() query that was causing type mismatch.

        This replicates the exact failing query pattern.
        """
        # Create a class
        class_entity = ClassEntity(
            name="TestClass",
            qualified_name="test_module.TestClass",
            file_path="/test/module.py",
            line_start=1,
            line_end=10,
            docstring="Test class",
            node_type=NodeType.CLASS,
            decorators=[],
            is_abstract=False,
            complexity=1,
        )

        clean_db.batch_create_nodes([class_entity])

        # This was the problematic pattern - size() on potentially null decorators
        # The issue: size(decorators) = 0 when decorators is stored as empty list
        # might cause FalkorDB type mismatch

        # Test 1: WITH + COALESCE pattern (the original failing query structure)
        try:
            query_with_coalesce = """
            MATCH (c:Class)
            WITH c, COALESCE(c.decorators, []) AS decorators
            WHERE size(decorators) = 0
            RETURN c.name AS name
            """
            results = clean_db.execute_query(query_with_coalesce)
            print(f"COALESCE pattern results: {len(results)}")
        except Exception as e:
            pytest.fail(f"COALESCE pattern failed: {e}")

        # Test 2: IS NULL OR size() pattern
        try:
            query_null_check = """
            MATCH (c:Class)
            WHERE c.decorators IS NULL OR size(c.decorators) = 0
            RETURN c.name AS name
            """
            results = clean_db.execute_query(query_null_check)
            print(f"IS NULL pattern results: {len(results)}")
        except Exception as e:
            pytest.fail(f"IS NULL pattern failed: {e}")

        # Test 3: Direct size() check (most likely to fail)
        try:
            query_direct = """
            MATCH (c:Class)
            WHERE size(c.decorators) = 0
            RETURN c.name AS name
            """
            results = clean_db.execute_query(query_direct)
            print(f"Direct size() pattern results: {len(results)}")
        except Exception as e:
            # This might fail - document the error
            print(f"Direct size() pattern error (expected): {e}")

    def test_decorators_stored_as_string(self, clean_db):
        """Test what happens when decorators is stored as a string instead of list.

        The production error "Type mismatch: expected Boolean but was String"
        might occur if some nodes have decorators stored as a string like "[]"
        or "" instead of an actual list.
        """
        # Manually create a node with decorators as a string
        query = """
        CREATE (c:Class {
            name: 'StringDecoratorClass',
            qualifiedName: 'test_module.StringDecoratorClass',
            filePath: '/test/module.py',
            lineStart: 1,
            lineEnd: 10,
            decorators: '[]'
        })
        RETURN c.name AS name
        """
        clean_db.execute_query(query)

        # Now try to use size() on this string - this should fail
        test_query = """
        MATCH (c:Class)
        WHERE c.name = 'StringDecoratorClass'
        RETURN c.decorators AS decorators, size(c.decorators) AS dec_size
        """

        try:
            results = clean_db.execute_query(test_query)
            print(f"String decorator results: {results}")
            # If it returns, the size() was computed on a string (length of "[]" = 2)
            if results:
                print(f"  decorators type: {type(results[0]['decorators'])}")
                print(f"  dec_size: {results[0]['dec_size']}")
        except Exception as e:
            print(f"String decorator error (EXPECTED): {e}")
            # This would confirm the type mismatch happens with string decorators

    def test_decorators_stored_as_null(self, clean_db):
        """Test what happens when decorators is NULL (not set at all)."""
        # Create a node without decorators property at all
        query = """
        CREATE (c:Class {
            name: 'NullDecoratorClass',
            qualifiedName: 'test_module.NullDecoratorClass',
            filePath: '/test/module.py',
            lineStart: 1,
            lineEnd: 10
        })
        RETURN c.name AS name
        """
        clean_db.execute_query(query)

        # Test size() on null
        test_query = """
        MATCH (c:Class)
        WHERE c.name = 'NullDecoratorClass'
        RETURN c.decorators AS decorators, size(COALESCE(c.decorators, [])) AS dec_size
        """

        try:
            results = clean_db.execute_query(test_query)
            print(f"Null decorator results: {results}")
            if results:
                print(f"  decorators: {results[0]['decorators']}")
                print(f"  dec_size: {results[0]['dec_size']}")
        except Exception as e:
            print(f"Null decorator error: {e}")

    def test_original_failing_query_structure(self, clean_db):
        """Test the exact query structure that was failing in production.

        The original error was:
        "Type mismatch: expected Boolean but was String"

        This tests the WITH + COALESCE + size() pattern that was used.
        """
        from repotoire.models import FileEntity

        # Create a file first (required for MATCH (file:File)-[:CONTAINS]->(c:Class))
        file_entity = FileEntity(
            name="module.py",
            qualified_name="/test/module.py",
            file_path="/test/module.py",
            line_start=1,
            line_end=100,
            docstring="",
            node_type=NodeType.FILE,
        )

        # Create a class with empty decorators
        class_entity = ClassEntity(
            name="TestClass",
            qualified_name="test_module.TestClass",
            file_path="/test/module.py",
            line_start=1,
            line_end=10,
            docstring="Test class",
            node_type=NodeType.CLASS,
            decorators=[],  # Empty list - the problematic case
            is_abstract=False,
            complexity=1,
        )

        clean_db.batch_create_nodes([file_entity, class_entity])

        # Create CONTAINS relationship
        from repotoire.models import Relationship, RelationshipType
        contains_rel = Relationship(
            source_id="/test/module.py",
            target_id="test_module.TestClass",
            rel_type=RelationshipType.CONTAINS,
        )
        clean_db.batch_create_relationships([contains_rel])

        # Test the EXACT original failing query structure
        # This was in _find_dead_classes before the fix
        original_failing_query = """
        MATCH (file:File)-[:CONTAINS]->(c:Class)
        WHERE NOT (c)<-[:CALLS]-()
          AND NOT (c)<-[:INHERITS]-()
          AND NOT (c)<-[:USES]-()
        OPTIONAL MATCH (file)-[:CONTAINS]->(m:Function)
        WHERE m.qualifiedName STARTS WITH c.qualifiedName + '.'
        WITH c, file, count(m) AS method_count, COALESCE(c.decorators, []) AS decorators
        WHERE size(decorators) = 0
        RETURN c.qualifiedName AS qualified_name,
               c.name AS name,
               c.decorators AS actual_decorators,
               method_count
        """

        # This might fail with the type mismatch error
        try:
            results = clean_db.execute_query(original_failing_query)
            print(f"Original failing query results: {len(results)}")
            for r in results:
                print(f"  {r}")
        except Exception as e:
            print(f"Original failing query error: {e}")
            # This is what we expect might fail in production
            # If it fails here, we've reproduced the issue

    def test_function_decorators_query(self, clean_db):
        """Test decorator queries for Function entities."""
        # Create functions
        functions = [
            FunctionEntity(
                name="decorated_func",
                qualified_name="test_module.decorated_func",
                file_path="/test/module.py",
                line_start=1,
                line_end=5,
                docstring="Decorated function",
                node_type=NodeType.FUNCTION,
                decorators=["lru_cache", "staticmethod"],
                parameters=[],
                return_type=None,
                is_async=False,
                is_method=False,
                is_static=True,
                is_classmethod=False,
                is_property=False,
                complexity=1,
            ),
            FunctionEntity(
                name="plain_func",
                qualified_name="test_module.plain_func",
                file_path="/test/module.py",
                line_start=10,
                line_end=15,
                docstring="Plain function",
                node_type=NodeType.FUNCTION,
                decorators=[],
                parameters=[],
                return_type=None,
                is_async=False,
                is_method=False,
                is_static=False,
                is_classmethod=False,
                is_property=False,
                complexity=1,
            ),
        ]

        clean_db.batch_create_nodes(functions)

        # Test query from _find_dead_functions
        query = """
        MATCH (f:Function)
        WHERE NOT (f)<-[:CALLS]-()
          AND NOT (f)<-[:USES]-()
        RETURN f.qualifiedName AS qualified_name,
               f.name AS name,
               f.decorators AS decorators
        """

        results = clean_db.execute_query(query)

        assert len(results) == 2

        # Verify Python-based filtering works
        for record in results:
            decorators = record.get("decorators")
            # Should be able to check decorators without type errors
            has_decorators = decorators and len(decorators) > 0
            print(f"Function {record['name']}: decorators={decorators}, has_decorators={has_decorators}")
