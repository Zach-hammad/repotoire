"""Unit tests for DependencyScanner."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import subprocess

from repotoire.security.dependency_scanner import DependencyScanner
from repotoire.models import Severity


@pytest.fixture
def mock_neo4j_client():
    """Create a mock Neo4j client."""
    client = Mock()
    client.execute_query = Mock(return_value=[])
    return client


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository with requirements file."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Create requirements.txt
    requirements = repo_path / "requirements.txt"
    requirements.write_text("requests==2.25.0\ndjango==3.1.0\n")

    return repo_path


class TestDependencyScannerInitialization:
    """Test DependencyScanner initialization."""

    def test_init_with_valid_path(self, mock_neo4j_client, temp_repo):
        """Test initialization with valid repository path."""
        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        assert scanner.repository_path == temp_repo
        assert scanner.requirements_file == "requirements.txt"
        assert scanner.max_findings == 100
        assert scanner.check_licenses is False

    def test_init_with_custom_config(self, mock_neo4j_client, temp_repo):
        """Test initialization with custom configuration."""
        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={
                "repository_path": str(temp_repo),
                "requirements_file": "requirements-dev.txt",
                "max_findings": 50,
                "check_licenses": True,
            }
        )

        assert scanner.requirements_file == "requirements-dev.txt"
        assert scanner.max_findings == 50
        assert scanner.check_licenses is True

    def test_init_with_invalid_path(self, mock_neo4j_client):
        """Test initialization with non-existent path raises error."""
        with pytest.raises(ValueError, match="does not exist"):
            DependencyScanner(
                mock_neo4j_client,
                detector_config={"repository_path": "/nonexistent/path"}
            )


class TestPipAuditExecution:
    """Test pip-audit execution and parsing."""

    @patch("subprocess.run")
    def test_run_pip_audit_with_vulnerabilities(self, mock_run, mock_neo4j_client, temp_repo):
        """Test running pip-audit with vulnerabilities found."""
        # Mock pip-audit JSON output
        mock_output = {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.25.0",
                    "vulns": [
                        {
                            "id": "PYSEC-2023-74",
                            "description": "Security vulnerability in requests",
                            "fix_versions": ["2.31.0"],
                            "aliases": ["CVE-2023-32681"]
                        }
                    ]
                }
            ]
        }

        mock_run.return_value = Mock(
            returncode=1,  # pip-audit returns 1 when vulns found
            stdout=json.dumps(mock_output),
            stderr=""
        )

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 1
        assert vulns[0]["name"] == "requests"
        assert vulns[0]["version"] == "2.25.0"
        assert len(vulns[0]["vulns"]) == 1

    @patch("subprocess.run")
    def test_run_pip_audit_no_vulnerabilities(self, mock_run, mock_neo4j_client, temp_repo):
        """Test running pip-audit with no vulnerabilities."""
        mock_output = {"dependencies": []}

        mock_run.return_value = Mock(
            returncode=0,  # Success, no vulnerabilities
            stdout=json.dumps(mock_output),
            stderr=""
        )

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 0

    @patch("subprocess.run")
    def test_run_pip_audit_command_failure(self, mock_run, mock_neo4j_client, temp_repo):
        """Test handling pip-audit command failure."""
        mock_run.return_value = Mock(
            returncode=2,  # Error code
            stdout="",
            stderr="pip-audit failed"
        )

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 0

    @patch("subprocess.run")
    def test_run_pip_audit_invalid_json(self, mock_run, mock_neo4j_client, temp_repo):
        """Test handling invalid JSON output."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="invalid json {{{",
            stderr=""
        )

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 0

    @patch("subprocess.run")
    def test_run_pip_audit_timeout(self, mock_run, mock_neo4j_client, temp_repo):
        """Test handling timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("pip-audit", 300)

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        with pytest.raises(subprocess.TimeoutExpired):
            scanner._run_pip_audit()


class TestFindingCreation:
    """Test finding creation from vulnerability data."""

    def test_create_finding_with_single_vuln(self, mock_neo4j_client, temp_repo):
        """Test creating finding from single vulnerability."""
        mock_neo4j_client.execute_query.return_value = [
            {"file_path": "app/views.py"},
            {"file_path": "tests/test_requests.py"}
        ]

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vuln_data = {
            "name": "requests",
            "version": "2.25.0",
            "vulns": [
                {
                    "id": "PYSEC-2023-74",
                    "description": "Remote code execution vulnerability",
                    "fix_versions": ["2.31.0"],
                    "aliases": ["CVE-2023-32681"]
                }
            ]
        }

        finding = scanner._create_finding(vuln_data)

        assert finding is not None
        assert finding.title == "Vulnerable dependency: requests 2.25.0 (PYSEC-2023-74)"
        assert "requests 2.25.0" in finding.description
        assert "PYSEC-2023-74" in finding.description
        assert "CVE-2023-32681" in finding.description
        assert finding.severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
        assert finding.detector == "dependency_scanner"
        assert finding.graph_context["package"] == "requests"
        assert finding.graph_context["version"] == "2.25.0"
        assert finding.graph_context["vulnerability_id"] == "PYSEC-2023-74"

    def test_create_finding_with_multiple_vulns(self, mock_neo4j_client, temp_repo):
        """Test creating finding with multiple vulnerabilities (uses most severe)."""
        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vuln_data = {
            "name": "django",
            "version": "3.1.0",
            "vulns": [
                {
                    "id": "VULN-1",
                    "description": "Minor issue",
                    "fix_versions": ["3.1.1", "3.1.2"],
                    "aliases": []
                },
                {
                    "id": "VULN-2",
                    "description": "Critical remote code execution",
                    "fix_versions": ["3.1.5"],
                    "aliases": ["CVE-2023-99999"]
                }
            ]
        }

        finding = scanner._create_finding(vuln_data)

        assert finding is not None
        # Should use the most severe vulnerability
        assert "django" in finding.title

    def test_create_finding_no_vulns(self, mock_neo4j_client, temp_repo):
        """Test creating finding with no vulnerabilities returns None."""
        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vuln_data = {
            "name": "safe-package",
            "version": "1.0.0",
            "vulns": []
        }

        finding = scanner._create_finding(vuln_data)

        assert finding is None


class TestSeverityDetermination:
    """Test severity determination from vulnerability data."""

    def test_determine_severity_from_cvss(self, mock_neo4j_client, temp_repo):
        """Test severity determination from CVSS severity string."""
        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Test CRITICAL
        vuln = {"severity": "CRITICAL", "description": "Test"}
        assert scanner._determine_severity(vuln) == Severity.CRITICAL

        # Test HIGH
        vuln = {"severity": "HIGH", "description": "Test"}
        assert scanner._determine_severity(vuln) == Severity.HIGH

        # Test MEDIUM
        vuln = {"severity": "MEDIUM", "description": "Test"}
        assert scanner._determine_severity(vuln) == Severity.MEDIUM

        # Test LOW
        vuln = {"severity": "LOW", "description": "Test"}
        assert scanner._determine_severity(vuln) == Severity.LOW

    def test_determine_severity_from_description(self, mock_neo4j_client, temp_repo):
        """Test severity determination from description keywords."""
        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Test critical keywords
        vuln = {"description": "Remote code execution vulnerability"}
        assert scanner._determine_severity(vuln) == Severity.CRITICAL

        # Test high keywords
        vuln = {"description": "SQL injection vulnerability"}
        assert scanner._determine_severity(vuln) == Severity.HIGH

        # Test medium keywords
        vuln = {"description": "Medium severity issue"}
        assert scanner._determine_severity(vuln) == Severity.MEDIUM

        # Test default (low)
        vuln = {"description": "Minor issue"}
        assert scanner._determine_severity(vuln) == Severity.LOW


class TestGraphEnrichment:
    """Test Neo4j graph enrichment."""

    def test_find_files_using_package(self, mock_neo4j_client, temp_repo):
        """Test finding files that import a package."""
        mock_neo4j_client.execute_query.return_value = [
            {"file_path": "app/api.py"},
            {"file_path": "app/views.py"},
            {"file_path": "tests/test_api.py"}
        ]

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        files = scanner._find_files_using_package("requests")

        assert len(files) == 3
        assert "app/api.py" in files
        assert "app/views.py" in files
        assert "tests/test_api.py" in files

        # Verify query was called with normalized package name
        mock_neo4j_client.execute_query.assert_called_once()
        call_args = mock_neo4j_client.execute_query.call_args
        assert call_args.kwargs["parameters"]["package_name"] == "requests"

    def test_find_files_using_package_normalizes_name(self, mock_neo4j_client, temp_repo):
        """Test package name normalization (hyphens to underscores)."""
        mock_neo4j_client.execute_query.return_value = []

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        scanner._find_files_using_package("some-package")

        call_args = mock_neo4j_client.execute_query.call_args
        assert call_args.kwargs["parameters"]["package_name"] == "some_package"

    def test_find_files_query_failure(self, mock_neo4j_client, temp_repo):
        """Test handling graph query failure."""
        mock_neo4j_client.execute_query.side_effect = Exception("Query failed")

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        files = scanner._find_files_using_package("requests")

        assert files == []


class TestDetectMethod:
    """Test the main detect() method."""

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_with_vulnerabilities(self, mock_run_audit, mock_neo4j_client, temp_repo):
        """Test detect() with vulnerabilities found."""
        mock_neo4j_client.execute_query.return_value = [{"file_path": "app/views.py"}]

        mock_run_audit.return_value = [
            {
                "name": "requests",
                "version": "2.25.0",
                "vulns": [
                    {
                        "id": "PYSEC-2023-74",
                        "description": "Security issue",
                        "fix_versions": ["2.31.0"],
                        "aliases": ["CVE-2023-32681"]
                    }
                ]
            }
        ]

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        findings = scanner.detect()

        assert len(findings) == 1
        assert findings[0].title.startswith("Vulnerable dependency: requests")

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_no_vulnerabilities(self, mock_run_audit, mock_neo4j_client, temp_repo):
        """Test detect() with no vulnerabilities."""
        mock_run_audit.return_value = []

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        findings = scanner.detect()

        assert len(findings) == 0

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_respects_max_findings(self, mock_run_audit, mock_neo4j_client, temp_repo):
        """Test that detect() respects max_findings limit."""
        # Create many vulnerabilities
        vulns = [
            {
                "name": f"package-{i}",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": f"VULN-{i}",
                        "description": "Issue",
                        "fix_versions": ["2.0.0"],
                        "aliases": []
                    }
                ]
            }
            for i in range(150)
        ]
        mock_run_audit.return_value = vulns

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={
                "repository_path": str(temp_repo),
                "max_findings": 10
            }
        )

        findings = scanner.detect()

        assert len(findings) <= 10

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_handles_errors(self, mock_run_audit, mock_neo4j_client, temp_repo):
        """Test detect() handles errors gracefully."""
        mock_run_audit.side_effect = Exception("Unexpected error")

        scanner = DependencyScanner(
            mock_neo4j_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        findings = scanner.detect()

        assert findings == []
