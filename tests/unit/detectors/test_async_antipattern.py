"""Tests for AsyncAntipatternDetector (REPO-228)."""

import pytest
from unittest.mock import Mock

from repotoire.detectors.async_antipattern import AsyncAntipatternDetector
from repotoire.models import Severity


class TestAsyncAntipatternDetector:
    """Test suite for AsyncAntipatternDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return AsyncAntipatternDetector(mock_client)

    def test_detects_blocking_sleep_in_async(self, detector, mock_client):
        """Should detect time.sleep() calls in async functions."""
        mock_client.execute_query.side_effect = [
            # First query: blocking calls
            [
                {
                    "func_name": "module.async_handler",
                    "func_simple_name": "async_handler",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "time.sleep",
                    "call_line": 15,
                    "all_calls": ["time.sleep"],
                }
            ],
            # Second query: wasteful async
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "blocking" in findings[0].title.lower()
        assert "time.sleep" in findings[0].graph_context["blocking_calls"]
        assert findings[0].severity in [Severity.MEDIUM, Severity.HIGH]

    def test_detects_requests_in_async(self, detector, mock_client):
        """Should detect requests.get() calls in async functions."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "api.fetch_data",
                    "func_simple_name": "fetch_data",
                    "func_file": "api.py",
                    "func_line": 20,
                    "containing_file": "api.py",
                    "call_name": "requests.get",
                    "call_line": 25,
                    "all_calls": ["requests.get"],
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "requests.get" in findings[0].graph_context["blocking_calls"]
        assert "aiohttp" in findings[0].suggested_fix.lower() or "httpx" in findings[0].suggested_fix.lower()

    def test_detects_multiple_blocking_calls(self, detector, mock_client):
        """Should detect multiple blocking calls in same async function."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.bad_async",
                    "func_simple_name": "bad_async",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "time.sleep",
                    "call_line": 15,
                    "all_calls": ["time.sleep", "requests.post"],
                },
                {
                    "func_name": "module.bad_async",
                    "func_simple_name": "bad_async",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "requests.post",
                    "call_line": 20,
                    "all_calls": ["time.sleep", "requests.post"],
                },
            ],
            [],
        ]

        findings = detector.detect()

        # Should create one finding with multiple blocking calls
        assert len(findings) == 1
        assert findings[0].graph_context["call_count"] == 2
        assert findings[0].severity == Severity.MEDIUM  # < 3 calls

    def test_high_severity_for_many_blocking_calls(self, detector, mock_client):
        """Should be HIGH severity when 3+ blocking calls detected."""
        blocking_calls = ["time.sleep", "requests.get", "subprocess.run"]
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.very_bad_async",
                    "func_simple_name": "very_bad_async",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": call,
                    "call_line": 15 + i,
                    "all_calls": blocking_calls,
                }
                for i, call in enumerate(blocking_calls)
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].graph_context["call_count"] == 3

    def test_detects_wasteful_async(self, detector, mock_client):
        """Should detect async functions with no await."""
        # The detector calls execute_query twice: once for blocking calls, once for wasteful async
        # Use a list for side_effect - each call pops the next value
        # Note: function name must NOT start with mock_, stub_, fake_ as those are skipped
        mock_client.execute_query.side_effect = [
            # First query: blocking calls (empty)
            [],
            # Second query: wasteful async
            [
                {
                    "func_name": "module.unnecessary_async",
                    "func_simple_name": "unnecessary_async",
                    "func_file": "module.py",
                    "func_line": 30,
                    "complexity": 5,
                    "containing_file": "module.py",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "wasteful" in findings[0].title.lower() or "no await" in findings[0].title.lower()
        assert findings[0].graph_context["pattern_type"] == "wasteful_async"
        assert findings[0].severity == Severity.MEDIUM

    def test_skips_legitimate_async_without_await(self, detector, mock_client):
        """Should not flag __aenter__ and other legitimate patterns."""
        mock_client.execute_query.side_effect = [
            [],
            [
                {
                    "func_name": "module.MyClass.__aenter__",
                    "func_simple_name": "__aenter__",
                    "func_file": "module.py",
                    "func_line": 30,
                    "complexity": 1,
                    "containing_file": "module.py",
                }
            ],
        ]

        findings = detector.detect()

        # Should not create finding for __aenter__
        assert len(findings) == 0

    def test_skips_mock_functions(self, detector, mock_client):
        """Should not flag mock_ prefixed functions."""
        mock_client.execute_query.side_effect = [
            [],
            [
                {
                    "func_name": "tests.mock_async_handler",
                    "func_simple_name": "mock_async_handler",
                    "func_file": "tests.py",
                    "func_line": 30,
                    "complexity": 1,
                    "containing_file": "tests.py",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_no_findings_for_clean_code(self, detector, mock_client):
        """Should return empty list for async code without issues."""
        mock_client.execute_query.side_effect = [
            [],  # No blocking calls
            [],  # No wasteful async
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_subprocess_detection(self, detector, mock_client):
        """Should detect subprocess calls in async functions."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.run_command",
                    "func_simple_name": "run_command",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "subprocess.run",
                    "call_line": 15,
                    "all_calls": ["subprocess.run"],
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "asyncio" in findings[0].suggested_fix.lower()

    def test_open_file_detection(self, detector, mock_client):
        """Should detect synchronous open() in async functions."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.read_file",
                    "func_simple_name": "read_file",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "open",
                    "call_line": 15,
                    "all_calls": ["open"],
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "aiofiles" in findings[0].suggested_fix.lower()

    def test_severity_method(self, detector, mock_client):
        """Should calculate severity from finding's pattern type."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.bad_async",
                    "func_simple_name": "bad_async",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "time.sleep",
                    "call_line": 15,
                    "all_calls": ["time.sleep"],
                }
            ],
            [],
        ]

        findings = detector.detect()
        severity = detector.severity(findings[0])

        # Blocking calls should be HIGH severity
        assert severity in [Severity.MEDIUM, Severity.HIGH]

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.bad_async",
                    "func_simple_name": "bad_async",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "time.sleep",
                    "call_line": 15,
                    "all_calls": ["time.sleep"],
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "AsyncAntipatternDetector"
        assert metadata.confidence >= 0.75
        assert "async_antipattern" in metadata.tags

    def test_config_overrides(self, mock_client):
        """Should allow config to override thresholds."""
        # Default behavior - we just verify the detector can be configured
        detector = AsyncAntipatternDetector(
            mock_client,
            detector_config={"max_async_without_await": 0}
        )

        assert detector.max_async_without_await == 0


class TestAsyncAntipatternDetectorWithEnricher:
    """Test AsyncAntipatternDetector with GraphEnricher."""

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
        detector = AsyncAntipatternDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.bad_async",
                    "func_simple_name": "bad_async",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "time.sleep",
                    "call_line": 15,
                    "all_calls": ["time.sleep"],
                }
            ],
            [],
        ]

        detector.detect()

        # Should have called flag_entity
        assert mock_enricher.flag_entity.called

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Should continue detection even if enricher fails."""
        detector = AsyncAntipatternDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.bad_async",
                    "func_simple_name": "bad_async",
                    "func_file": "module.py",
                    "func_line": 10,
                    "containing_file": "module.py",
                    "call_name": "time.sleep",
                    "call_line": 15,
                    "all_calls": ["time.sleep"],
                }
            ],
            [],
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1


class TestBlockingCallPatterns:
    """Test blocking call pattern detection."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return AsyncAntipatternDetector(mock_client)

    def test_get_blocking_alternative_exact_match(self, detector):
        """Should return alternative for exact matches."""
        alt = detector._get_blocking_alternative("time.sleep")
        assert alt == "asyncio.sleep"

        alt = detector._get_blocking_alternative("requests.get")
        assert "aiohttp" in alt.lower() or "httpx" in alt.lower()

    def test_get_blocking_alternative_pattern_match(self, detector):
        """Should return alternative for pattern matches."""
        alt = detector._get_blocking_alternative("requests.session")
        assert alt is not None

        alt = detector._get_blocking_alternative("subprocess.check_call")
        assert alt is not None

    def test_get_blocking_alternative_no_match(self, detector):
        """Should return None for non-blocking calls."""
        alt = detector._get_blocking_alternative("print")
        assert alt is None

        alt = detector._get_blocking_alternative("asyncio.sleep")
        assert alt is None
