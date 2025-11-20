"""Integration tests for Neo4j connection pooling and query timeouts."""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from neo4j.exceptions import ServiceUnavailable

from repotoire.graph.client import Neo4jClient
from repotoire.models import FileEntity, Relationship, RelationshipType


class TestConnectionPooling:
    """Test Neo4j connection pool configuration."""

    def test_client_initialization_with_pool_settings(self):
        """Test client initializes with custom pool settings."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient(
                uri="bolt://localhost:7687",
                username="neo4j",
                password="test",
                max_connection_pool_size=100,
                connection_timeout=15.0,
                max_connection_lifetime=7200,
                query_timeout=30.0,
                encrypted=True,
            )

            # Verify driver was created with correct pool settings
            mock_driver_class.assert_called_once()
            call_kwargs = mock_driver_class.call_args[1]

            assert call_kwargs["max_connection_pool_size"] == 100
            assert call_kwargs["connection_timeout"] == 15.0
            assert call_kwargs["max_connection_lifetime"] == 7200
            assert call_kwargs["encrypted"] is True
            assert call_kwargs["keep_alive"] is True
            assert client.query_timeout == 30.0

    def test_default_pool_settings(self):
        """Test client uses sensible defaults for pool settings."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient()

            call_kwargs = mock_driver_class.call_args[1]

            # Check defaults match MVP recommendations
            assert call_kwargs["max_connection_pool_size"] == 50
            assert call_kwargs["connection_timeout"] == 30.0
            assert call_kwargs["max_connection_lifetime"] == 3600
            assert call_kwargs["encrypted"] is False
            assert client.query_timeout == 60.0

    def test_query_timeout_enforcement(self):
        """Test execute_query enforces timeout."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_session = MagicMock()
            mock_result = Mock()
            mock_result.__iter__ = Mock(return_value=iter([{"count": 5}]))
            mock_session.run.return_value = mock_result

            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()
            mock_driver.session.return_value.__enter__ = Mock(return_value=mock_session)
            mock_driver.session.return_value.__exit__ = Mock(return_value=None)
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient(query_timeout=10.0)

            # Execute query with default timeout
            client.execute_query("MATCH (n) RETURN count(n) as count")

            # Verify timeout was passed (in milliseconds)
            assert mock_session.run.call_args[1]["timeout"] == 10000

    def test_custom_query_timeout_override(self):
        """Test execute_query can override default timeout."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_session = MagicMock()
            mock_result = Mock()
            mock_result.__iter__ = Mock(return_value=iter([{"count": 5}]))
            mock_session.run.return_value = mock_result

            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()
            mock_driver.session.return_value.__enter__ = Mock(return_value=mock_session)
            mock_driver.session.return_value.__exit__ = Mock(return_value=None)
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient(query_timeout=60.0)

            # Execute query with custom timeout
            client.execute_query("MATCH (n) RETURN count(n) as count", timeout=5.0)

            # Verify custom timeout was used (in milliseconds)
            assert mock_session.run.call_args[1]["timeout"] == 5000

    def test_get_pool_metrics(self):
        """Test get_pool_metrics returns configuration."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_pool = Mock()
            mock_pool.in_use_connection_count = 3
            mock_pool.idle_count = 7

            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()
            mock_driver._pool = mock_pool
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient(
                max_connection_pool_size=100,
                connection_timeout=15.0,
                max_connection_lifetime=7200,
                query_timeout=30.0,
                encrypted=True,
            )

            metrics = client.get_pool_metrics()

            assert metrics["max_size"] == 100
            assert metrics["acquisition_timeout"] == 15.0
            assert metrics["max_lifetime"] == 7200
            assert metrics["query_timeout"] == 30.0
            assert metrics["encrypted"] is True
            assert metrics["in_use"] == 3
            assert metrics["idle"] == 7


class TestWriteTransactions:
    """Test write transactions for batch operations."""

    def test_batch_create_nodes_uses_write_transaction(self):
        """Test batch_create_nodes uses session.execute_write."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_session = MagicMock()
            mock_session.execute_write = Mock(return_value=[
                {"id": "elem-1", "qualifiedName": "test.py"}
            ])

            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()
            mock_driver.session.return_value.__enter__ = Mock(return_value=mock_session)
            mock_driver.session.return_value.__exit__ = Mock(return_value=None)
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient()

            entities = [
                FileEntity(
                    name="test.py",
                    qualified_name="test.py",
                    file_path="test.py",
                    line_start=0,
                    line_end=10,
                )
            ]

            result = client.batch_create_nodes(entities)

            # Verify execute_write was called
            assert mock_session.execute_write.called
            assert "test.py" in result
            assert result["test.py"] == "elem-1"

    def test_batch_create_relationships_uses_write_transaction(self):
        """Test batch_create_relationships uses session.execute_write."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_session = MagicMock()
            mock_consume_result = Mock()
            mock_consume_result.counters.relationships_created = 2

            mock_result = Mock()
            mock_result.consume.return_value = mock_consume_result

            mock_tx = Mock()
            mock_tx.run.return_value = mock_result

            # execute_write calls the function with tx
            def execute_write_side_effect(func, *args, **kwargs):
                return func(mock_tx, *args, **kwargs)

            mock_session.execute_write = Mock(side_effect=execute_write_side_effect)

            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()
            mock_driver.session.return_value.__enter__ = Mock(return_value=mock_session)
            mock_driver.session.return_value.__exit__ = Mock(return_value=None)
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient()

            relationships = [
                Relationship(
                    source_id="file1.py",
                    target_id="file2.py",
                    rel_type=RelationshipType.IMPORTS,
                    properties={}
                ),
                Relationship(
                    source_id="file1.py",
                    target_id="os",
                    rel_type=RelationshipType.IMPORTS,
                    properties={}
                )
            ]

            count = client.batch_create_relationships(relationships)

            # Verify execute_write was called
            assert mock_session.execute_write.called
            assert count == 2


class TestRetryLogic:
    """Test retry logic for transient failures."""

    def test_connection_retry_with_exponential_backoff(self):
        """Test connection retries with exponential backoff."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            with patch('falkor.graph.client.time.sleep') as mock_sleep:
                # Fail first 2 attempts, succeed on 3rd
                mock_driver = Mock()
                mock_driver.verify_connectivity.side_effect = [
                    ServiceUnavailable("Connection refused"),
                    ServiceUnavailable("Connection refused"),
                    None,  # Success
                ]
                mock_driver_class.return_value = mock_driver

                client = Neo4jClient(
                    max_retries=3,
                    retry_base_delay=1.0,
                    retry_backoff_factor=2.0
                )

                # Verify exponential backoff delays
                assert mock_sleep.call_count == 2
                delays = [call[0][0] for call in mock_sleep.call_args_list]
                assert delays[0] == 1.0  # First retry: 1.0 * 2^0
                assert delays[1] == 2.0  # Second retry: 1.0 * 2^1

    def test_connection_fails_after_max_retries(self):
        """Test connection raises error after max retries."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            with patch('falkor.graph.client.time.sleep'):
                mock_driver = Mock()
                mock_driver.verify_connectivity.side_effect = ServiceUnavailable("Connection refused")
                mock_driver_class.return_value = mock_driver

                with pytest.raises(ServiceUnavailable, match="after 3 attempts"):
                    Neo4jClient(max_retries=3)

    def test_query_retry_on_transient_error(self):
        """Test queries are retried on transient errors."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            with patch('falkor.graph.client.time.sleep') as mock_sleep:
                mock_session = MagicMock()

                # Fail first attempt, succeed on second
                mock_result_success = Mock()
                mock_result_success.__iter__ = Mock(return_value=iter([{"count": 5}]))

                mock_session.run.side_effect = [
                    ServiceUnavailable("Transient error"),
                    mock_result_success,
                ]

                mock_driver = Mock()
                mock_driver.verify_connectivity = Mock()
                mock_driver.session.return_value.__enter__ = Mock(return_value=mock_session)
                mock_driver.session.return_value.__exit__ = Mock(return_value=None)
                mock_driver_class.return_value = mock_driver

                client = Neo4jClient(max_retries=3, retry_base_delay=0.1)

                result = client.execute_query("MATCH (n) RETURN count(n) as count")

                # Verify query was retried
                assert mock_session.run.call_count == 2
                assert result == [{"count": 5}]
                assert mock_sleep.called


class TestConcurrentConnections:
    """Test connection pool behavior under concurrent load."""

    def test_concurrent_queries_share_pool(self):
        """Test multiple queries can run concurrently using pool."""
        with patch('falkor.graph.client.GraphDatabase.driver') as mock_driver_class:
            mock_sessions = []

            for i in range(5):
                mock_session = MagicMock()
                mock_result = Mock()
                mock_result.__iter__ = Mock(return_value=iter([{"id": i}]))
                mock_session.run.return_value = mock_result
                mock_sessions.append(mock_session)

            mock_driver = Mock()
            mock_driver.verify_connectivity = Mock()

            # Return different sessions for each call
            session_iter = iter(mock_sessions)
            def get_session(*args, **kwargs):
                session = next(session_iter)
                context = MagicMock()
                context.__enter__ = Mock(return_value=session)
                context.__exit__ = Mock(return_value=None)
                return context

            mock_driver.session = get_session
            mock_driver_class.return_value = mock_driver

            client = Neo4jClient(max_connection_pool_size=10)

            # Execute multiple queries
            results = []
            for i in range(5):
                result = client.execute_query(f"MATCH (n) WHERE id(n) = {i} RETURN n")
                results.append(result)

            # Verify all queries completed
            assert len(results) == 5
            for i, result in enumerate(results):
                assert result == [{"id": i}]
