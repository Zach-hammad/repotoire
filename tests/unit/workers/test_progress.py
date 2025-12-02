"""Unit tests for progress tracking."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.db.models import AnalysisStatus


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    with patch("repotoire.workers.progress.redis.from_url") as mock:
        redis_client = MagicMock()
        mock.return_value = redis_client
        yield redis_client


@pytest.fixture
def mock_session():
    """Mock database session."""
    with patch("repotoire.workers.progress.get_sync_session") as mock:
        session = MagicMock()
        mock.return_value.__enter__ = MagicMock(return_value=session)
        mock.return_value.__exit__ = MagicMock(return_value=False)
        yield session


class TestProgressTracker:
    """Tests for ProgressTracker class."""

    def test_init(self):
        """Test tracker initialization."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        tracker = ProgressTracker(task, analysis_run_id)

        assert tracker.task == task
        assert tracker.analysis_run_id == analysis_run_id
        assert tracker.channel == f"analysis:{analysis_run_id}"
        assert tracker._redis is None  # Lazy initialization

    def test_update_status(self, mock_session, mock_redis):
        """Test updating status."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis):
            tracker = ProgressTracker(task, analysis_run_id)

            tracker.update(
                status=AnalysisStatus.RUNNING,
                progress_percent=50,
                current_step="Analyzing code",
            )

            # Verify database update was called
            mock_session.execute.assert_called_once()

            # Verify Redis publish was called
            mock_redis.publish.assert_called_once()
            call_args = mock_redis.publish.call_args
            assert call_args[0][0] == f"analysis:{analysis_run_id}"

            # Verify task state was updated
            task.update_state.assert_called_once_with(
                state="PROGRESS",
                meta={
                    "progress_percent": 50,
                    "current_step": "Analyzing code",
                },
            )

    def test_update_with_error(self, mock_session, mock_redis):
        """Test updating with error message."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis):
            tracker = ProgressTracker(task, analysis_run_id)

            tracker.update(
                status=AnalysisStatus.FAILED,
                error_message="Something went wrong",
            )

            # Verify error message was included in update
            mock_session.execute.assert_called_once()

    def test_update_without_task(self, mock_session, mock_redis):
        """Test updating without a Celery task (for testing)."""
        from repotoire.workers.progress import ProgressTracker

        analysis_run_id = str(uuid4())

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis):
            tracker = ProgressTracker(None, analysis_run_id)

            # Should not raise even without task
            tracker.update(
                progress_percent=25,
                current_step="Testing",
            )

            # DB and Redis should still work
            mock_session.execute.assert_called_once()
            mock_redis.publish.assert_called_once()

    def test_broadcast_redis_message_format(self, mock_session, mock_redis):
        """Test the format of Redis pub/sub messages."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis):
            tracker = ProgressTracker(task, analysis_run_id)

            tracker.update(
                status=AnalysisStatus.RUNNING,
                progress_percent=75,
                current_step="Final step",
            )

            # Get the published message
            call_args = mock_redis.publish.call_args
            message = json.loads(call_args[0][1])

            assert message["analysis_run_id"] == analysis_run_id
            assert message["status"] == "running"
            assert message["progress_percent"] == 75
            assert message["current_step"] == "Final step"
            assert "timestamp" in message

    def test_db_failure_doesnt_block_redis(self, mock_session, mock_redis):
        """Test that DB failure doesn't prevent Redis broadcast."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        mock_session.execute.side_effect = Exception("DB error")

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis):
            tracker = ProgressTracker(task, analysis_run_id)

            # Should not raise
            tracker.update(
                progress_percent=50,
                current_step="Testing",
            )

            # Redis should still be called
            mock_redis.publish.assert_called_once()

    def test_redis_failure_doesnt_raise(self, mock_session, mock_redis):
        """Test that Redis failure is handled gracefully."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        mock_redis.publish.side_effect = Exception("Redis error")

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis):
            tracker = ProgressTracker(task, analysis_run_id)

            # Should not raise
            tracker.update(
                progress_percent=50,
                current_step="Testing",
            )

            # Task state should still be updated
            task.update_state.assert_called_once()

    def test_close(self, mock_redis):
        """Test closing Redis connection."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis):
            tracker = ProgressTracker(task, analysis_run_id)
            tracker._redis = mock_redis  # Simulate connection

            tracker.close()

            mock_redis.close.assert_called_once()
            assert tracker._redis is None

    def test_close_without_connection(self):
        """Test closing when no connection exists."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        tracker = ProgressTracker(task, analysis_run_id)

        # Should not raise
        tracker.close()

    def test_lazy_redis_initialization(self, mock_redis):
        """Test Redis is initialized lazily on first use."""
        from repotoire.workers.progress import ProgressTracker

        task = MagicMock()
        analysis_run_id = str(uuid4())

        with patch("repotoire.workers.progress.redis.from_url", return_value=mock_redis) as mock_from_url:
            tracker = ProgressTracker(task, analysis_run_id)

            # Redis not initialized yet
            assert tracker._redis is None
            mock_from_url.assert_not_called()

            # Access redis property
            _ = tracker.redis

            # Now Redis should be initialized
            mock_from_url.assert_called_once()
            assert tracker._redis == mock_redis
