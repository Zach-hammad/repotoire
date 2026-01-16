"""Tests for npm audit security detector."""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from repotoire.detectors.npm_audit_detector import NpmAuditDetector
from repotoire.models import Severity


@pytest.fixture
def mock_graph_client():
    """Create a mock graph client."""
    client = MagicMock()
    client.execute_query.return_value = [
        {
            "file_path": "src/utils.ts",
        }
    ]
    return client


@pytest.fixture
def temp_repo():
    """Create a temporary repository with package.json and package-lock.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create a package.json
        package_json = repo_path / "package.json"
        package_json.write_text(json.dumps({
            "name": "test-project",
            "version": "1.0.0",
            "dependencies": {
                "lodash": "^4.17.0",
                "axios": "^0.21.0",
            }
        }))

        # Create a package-lock.json (required for npm audit)
        package_lock = repo_path / "package-lock.json"
        package_lock.write_text(json.dumps({
            "name": "test-project",
            "version": "1.0.0",
            "lockfileVersion": 2,
            "requires": True,
            "packages": {
                "": {
                    "name": "test-project",
                    "version": "1.0.0",
                    "dependencies": {
                        "lodash": "^4.17.0",
                        "axios": "^0.21.0",
                    }
                }
            }
        }))

        # Create a file that imports a dependency
        ts_file = repo_path / "src" / "utils.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text('''
import lodash from 'lodash';
import axios from 'axios';

export function getData() {
    return axios.get('/api/data');
}
''')

        yield repo_path


class TestNpmAuditDetector:
    """Test NpmAuditDetector functionality."""

    def test_detector_initialization(self, mock_graph_client, temp_repo):
        """Test detector can be initialized."""
        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector.repository_path == temp_repo
        assert detector.max_findings == 100
        assert detector.min_severity == "low"
        assert detector.production_only is False

    def test_detector_invalid_path(self, mock_graph_client):
        """Test detector raises error for invalid path."""
        with pytest.raises(ValueError, match="does not exist"):
            NpmAuditDetector(
                graph_client=mock_graph_client,
                detector_config={"repository_path": "/nonexistent/path"},
            )

    def test_severity_mapping(self, mock_graph_client, temp_repo):
        """Test npm audit severity mapping."""
        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector.SEVERITY_MAP["critical"] == Severity.CRITICAL
        assert detector.SEVERITY_MAP["high"] == Severity.HIGH
        assert detector.SEVERITY_MAP["moderate"] == Severity.MEDIUM
        assert detector.SEVERITY_MAP["low"] == Severity.LOW
        assert detector.SEVERITY_MAP["info"] == Severity.INFO

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_detect_with_vulnerabilities_v7_format(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection with npm v7+ audit format."""
        # Mock npm audit v7+ output
        audit_output = {
            "vulnerabilities": {
                "lodash": {
                    "severity": "high",
                    "via": [
                        {
                            "title": "Prototype Pollution",
                            "url": "https://npmjs.com/advisories/123",
                            "cwe": ["CWE-1321"],
                            "range": "<4.17.21"
                        }
                    ],
                    "range": "<4.17.21",
                    "fixAvailable": True
                },
                "axios": {
                    "severity": "moderate",
                    "via": [
                        {
                            "title": "Server-Side Request Forgery",
                            "url": "https://npmjs.com/advisories/456",
                            "cwe": ["CWE-918"],
                            "range": "<0.21.2"
                        }
                    ],
                    "range": "<0.21.2",
                    "fixAvailable": True
                }
            }
        }

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps(audit_output)
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()

        assert len(findings) == 2

        # Check high severity finding
        lodash_finding = next(f for f in findings if "lodash" in f.title)
        assert lodash_finding.detector == "NpmAuditDetector"
        assert lodash_finding.severity == Severity.HIGH
        assert "Prototype Pollution" in lodash_finding.description
        assert "npm audit fix" in lodash_finding.suggested_fix

        # Check moderate severity finding
        axios_finding = next(f for f in findings if "axios" in f.title)
        assert axios_finding.severity == Severity.MEDIUM

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_detect_with_vulnerabilities_v6_format(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection with npm v6 audit format."""
        # Mock npm audit v6 output
        audit_output = {
            "advisories": {
                "123": {
                    "module_name": "lodash",
                    "severity": "high",
                    "title": "Prototype Pollution",
                    "url": "https://npmjs.com/advisories/123",
                    "cwe": "CWE-1321",
                    "vulnerable_versions": "<4.17.21",
                    "patched_versions": ">=4.17.21"
                }
            }
        }

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps(audit_output)
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()

        assert len(findings) == 1
        assert "lodash" in findings[0].title
        assert findings[0].severity == Severity.HIGH

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_detect_no_vulnerabilities(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection with no vulnerabilities."""
        audit_output = {"vulnerabilities": {}}

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps(audit_output)
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_detect_timeout(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection handles timeout gracefully."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.timed_out = True
        mock_result.stdout = ""
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_min_severity_filter(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test min_severity filter."""
        audit_output = {
            "vulnerabilities": {
                "package1": {
                    "severity": "critical",
                    "via": [{"title": "Critical vuln", "url": ""}],
                    "fixAvailable": True
                },
                "package2": {
                    "severity": "high",
                    "via": [{"title": "High vuln", "url": ""}],
                    "fixAvailable": True
                },
                "package3": {
                    "severity": "low",
                    "via": [{"title": "Low vuln", "url": ""}],
                    "fixAvailable": True
                }
            }
        }

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps(audit_output)
        mock_run_tool.return_value = mock_result

        # Only report high severity and above
        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "min_severity": "high",
            },
        )

        findings = detector.detect()

        assert len(findings) == 2  # critical and high
        severities = {f.severity for f in findings}
        assert Severity.LOW not in severities

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_production_only_flag(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test production_only flag is passed to npm audit."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps({"vulnerabilities": {}})
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "production_only": True,
            },
        )

        detector.detect()

        # Verify --omit=dev was passed
        call_args = mock_run_tool.call_args
        cmd = call_args.kwargs["cmd"]
        assert "--omit=dev" in cmd

    def test_max_findings_limit(self, mock_graph_client, temp_repo):
        """Test max_findings configuration."""
        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "max_findings": 5,
            },
        )

        assert detector.max_findings == 5

    def test_no_package_json_skips(self, mock_graph_client):
        """Test detector skips when no package.json exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            detector = NpmAuditDetector(
                graph_client=mock_graph_client,
                detector_config={"repository_path": str(repo_path)},
            )

            findings = detector.detect()
            assert len(findings) == 0

    def test_suggest_fix_with_fix_available(self, mock_graph_client, temp_repo):
        """Test fix suggestion when fix is available."""
        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        vuln = {
            "package": "lodash",
            "fix_available": True,
        }

        suggestion = detector._suggest_fix(vuln)
        assert "npm audit fix" in suggestion

    def test_suggest_fix_no_fix_available(self, mock_graph_client, temp_repo):
        """Test fix suggestion when no fix is available."""
        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        vuln = {
            "package": "problematic-package",
            "fix_available": False,
        }

        suggestion = detector._suggest_fix(vuln)
        assert "alternative packages" in suggestion or "workarounds" in suggestion

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_build_description(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test description building."""
        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        vuln = {
            "package": "lodash",
            "severity": "high",
            "title": "Prototype Pollution",
            "url": "https://npmjs.com/advisories/123",
            "cwe": ["CWE-1321"],
            "range": "<4.17.21",
        }

        affected_files = ["src/utils.ts"]

        desc = detector._build_description(vuln, affected_files)

        assert "Prototype Pollution" in desc
        assert "lodash" in desc
        assert "HIGH" in desc
        assert "<4.17.21" in desc
        assert "src/utils.ts" in desc
        assert "CWE-1321" in desc

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_via_string_reference(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test handling of via strings (references to other packages)."""
        # When vulnerability is via another package
        audit_output = {
            "vulnerabilities": {
                "nested-dep": {
                    "severity": "high",
                    "via": ["lodash"],  # String reference to another package
                    "range": "*",
                    "fixAvailable": True
                }
            }
        }

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps(audit_output)
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()

        assert len(findings) == 1
        assert "Via lodash" in findings[0].description

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_invalid_json_handled(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test handling of invalid JSON output."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = "not valid json"
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    def test_severity_method(self, mock_graph_client, temp_repo):
        """Test severity method returns finding's severity."""
        from repotoire.models import Finding

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        finding = Finding(
            id="test-id",
            detector="NpmAuditDetector",
            severity=Severity.CRITICAL,
            title="Test",
            description="Test",
            affected_nodes=[],
            affected_files=["package.json"],
        )

        assert detector.severity(finding) == Severity.CRITICAL

    @patch("repotoire.detectors.npm_audit_detector.run_external_tool")
    def test_collaboration_metadata(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test that collaboration metadata is added to findings."""
        audit_output = {
            "vulnerabilities": {
                "lodash": {
                    "severity": "high",
                    "via": [
                        {
                            "title": "Prototype Pollution",
                            "url": "https://npmjs.com/advisories/123",
                        }
                    ],
                    "fixAvailable": True
                }
            }
        }

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps(audit_output)
        mock_run_tool.return_value = mock_result

        detector = NpmAuditDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()

        assert len(findings) == 1
        assert len(findings[0].collaboration_metadata) > 0
        collab = findings[0].collaboration_metadata[0]
        assert collab.detector == "NpmAuditDetector"
        assert collab.confidence == 0.95
        assert "security" in collab.tags
        assert "npm_audit" in collab.tags
