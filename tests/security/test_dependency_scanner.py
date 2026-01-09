"""Unit tests for DependencyScanner."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import subprocess

from repotoire.security.dependency_scanner import DependencyScanner
from repotoire.models import Severity


@pytest.fixture
def mock_graph_client():
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

    def test_init_with_valid_path(self, mock_graph_client, temp_repo):
        """Test initialization with valid repository path."""
        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        assert scanner.repository_path == temp_repo
        assert scanner.requirements_file == "requirements.txt"
        assert scanner.max_findings == 100
        assert scanner.check_licenses is False

    def test_init_with_custom_config(self, mock_graph_client, temp_repo):
        """Test initialization with custom configuration."""
        scanner = DependencyScanner(
            mock_graph_client,
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

    def test_init_with_invalid_path(self, mock_graph_client):
        """Test initialization with non-existent path raises error."""
        with pytest.raises(ValueError, match="does not exist"):
            DependencyScanner(
                mock_graph_client,
                detector_config={"repository_path": "/nonexistent/path"}
            )


class TestPipAuditExecution:
    """Test pip-audit execution and parsing."""

    @patch("subprocess.run")
    def test_run_pip_audit_with_vulnerabilities(self, mock_run, mock_graph_client, temp_repo):
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
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 1
        assert vulns[0]["name"] == "requests"
        assert vulns[0]["version"] == "2.25.0"
        assert len(vulns[0]["vulns"]) == 1

    @patch("subprocess.run")
    def test_run_pip_audit_no_vulnerabilities(self, mock_run, mock_graph_client, temp_repo):
        """Test running pip-audit with no vulnerabilities."""
        mock_output = {"dependencies": []}

        mock_run.return_value = Mock(
            returncode=0,  # Success, no vulnerabilities
            stdout=json.dumps(mock_output),
            stderr=""
        )

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 0

    @patch("subprocess.run")
    def test_run_pip_audit_command_failure(self, mock_run, mock_graph_client, temp_repo):
        """Test handling pip-audit command failure."""
        mock_run.return_value = Mock(
            returncode=2,  # Error code
            stdout="",
            stderr="pip-audit failed"
        )

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 0

    @patch("subprocess.run")
    def test_run_pip_audit_invalid_json(self, mock_run, mock_graph_client, temp_repo):
        """Test handling invalid JSON output."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="invalid json {{{",
            stderr=""
        )

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        vulns = scanner._run_pip_audit()

        assert len(vulns) == 0

    @patch("subprocess.run")
    def test_run_pip_audit_timeout(self, mock_run, mock_graph_client, temp_repo):
        """Test handling timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("pip-audit", 300)

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        with pytest.raises(subprocess.TimeoutExpired):
            scanner._run_pip_audit()


class TestFindingCreation:
    """Test finding creation from vulnerability data."""

    def test_create_finding_with_single_vuln(self, mock_graph_client, temp_repo):
        """Test creating finding from single vulnerability."""
        mock_graph_client.execute_query.return_value = [
            {"file_path": "app/views.py"},
            {"file_path": "tests/test_requests.py"}
        ]

        scanner = DependencyScanner(
            mock_graph_client,
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

    def test_create_finding_with_multiple_vulns(self, mock_graph_client, temp_repo):
        """Test creating finding with multiple vulnerabilities (uses most severe)."""
        scanner = DependencyScanner(
            mock_graph_client,
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

    def test_create_finding_no_vulns(self, mock_graph_client, temp_repo):
        """Test creating finding with no vulnerabilities returns None."""
        scanner = DependencyScanner(
            mock_graph_client,
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

    def test_determine_severity_from_cvss(self, mock_graph_client, temp_repo):
        """Test severity determination from CVSS severity string."""
        scanner = DependencyScanner(
            mock_graph_client,
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

    def test_determine_severity_from_description(self, mock_graph_client, temp_repo):
        """Test severity determination from description keywords."""
        scanner = DependencyScanner(
            mock_graph_client,
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

    def test_find_files_using_package(self, mock_graph_client, temp_repo):
        """Test finding files that import a package."""
        mock_graph_client.execute_query.return_value = [
            {"file_path": "app/api.py"},
            {"file_path": "app/views.py"},
            {"file_path": "tests/test_api.py"}
        ]

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        files = scanner._find_files_using_package("requests")

        assert len(files) == 3
        assert "app/api.py" in files
        assert "app/views.py" in files
        assert "tests/test_api.py" in files

        # Verify query was called with normalized package name
        mock_graph_client.execute_query.assert_called_once()
        call_args = mock_graph_client.execute_query.call_args
        assert call_args.kwargs["parameters"]["package_name"] == "requests"

    def test_find_files_using_package_normalizes_name(self, mock_graph_client, temp_repo):
        """Test package name normalization (hyphens to underscores)."""
        mock_graph_client.execute_query.return_value = []

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        scanner._find_files_using_package("some-package")

        call_args = mock_graph_client.execute_query.call_args
        assert call_args.kwargs["parameters"]["package_name"] == "some_package"

    def test_find_files_query_failure(self, mock_graph_client, temp_repo):
        """Test handling graph query failure."""
        mock_graph_client.execute_query.side_effect = Exception("Query failed")

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        files = scanner._find_files_using_package("requests")

        assert files == []


class TestDetectMethod:
    """Test the main detect() method."""

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_with_vulnerabilities(self, mock_run_audit, mock_graph_client, temp_repo):
        """Test detect() with vulnerabilities found."""
        mock_graph_client.execute_query.return_value = [{"file_path": "app/views.py"}]

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
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        findings = scanner.detect()

        assert len(findings) == 1
        assert findings[0].title.startswith("Vulnerable dependency: requests")

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_no_vulnerabilities(self, mock_run_audit, mock_graph_client, temp_repo):
        """Test detect() with no vulnerabilities."""
        mock_run_audit.return_value = []

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        findings = scanner.detect()

        assert len(findings) == 0

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_respects_max_findings(self, mock_run_audit, mock_graph_client, temp_repo):
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
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "max_findings": 10
            }
        )

        findings = scanner.detect()

        assert len(findings) <= 10

    @patch.object(DependencyScanner, "_run_pip_audit")
    def test_detect_handles_errors(self, mock_run_audit, mock_graph_client, temp_repo):
        """Test detect() handles errors gracefully."""
        mock_run_audit.side_effect = Exception("Unexpected error")

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        findings = scanner.detect()

        assert findings == []


class TestSafetyFallback:
    """Test safety fallback when pip-audit is not available (REPO-413)."""

    @patch("subprocess.run")
    def test_safety_fallback_when_pip_audit_not_found(self, mock_run, mock_graph_client, temp_repo):
        """Test fallback to safety when pip-audit is not installed."""
        # pip-audit raises FileNotFoundError, safety returns vulnerabilities
        safety_output = [
            ["requests", "<2.31.0", "2.25.0", "Security vulnerability in requests", 12345]
        ]

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "uv-secure" in cmd:
                raise FileNotFoundError("uv-secure not found")
            elif "pip-audit" in cmd:
                raise FileNotFoundError("pip-audit not found")
            elif "safety" in cmd:
                return Mock(
                    returncode=64,  # vulnerabilities found
                    stdout=json.dumps(safety_output),
                    stderr=""
                )

        mock_run.side_effect = side_effect

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Test via _run_vulnerability_scan which orchestrates fallbacks
        vulns = scanner._run_vulnerability_scan()

        assert len(vulns) == 1
        assert vulns[0]["name"] == "requests"
        assert vulns[0]["version"] == "2.25.0"
        assert len(vulns[0]["vulns"]) == 1

    @patch("subprocess.run")
    def test_safety_fallback_v3_format(self, mock_run, mock_graph_client, temp_repo):
        """Test safety fallback with v3 JSON format."""
        safety_v3_output = {
            "report_meta": {"scan_target": "environment"},
            "vulnerabilities": [
                {
                    "package_name": "django",
                    "installed_version": "3.1.0",
                    "vulnerability_id": "CVE-2023-12345",
                    "advisory": "SQL injection vulnerability",
                    "fixed_versions": ["3.2.0", "3.1.1"],
                    "cve": ["CVE-2023-12345"]
                }
            ]
        }

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "uv-secure" in cmd:
                raise FileNotFoundError("uv-secure not found")
            elif "pip-audit" in cmd:
                raise FileNotFoundError("pip-audit not found")
            elif "safety" in cmd:
                return Mock(
                    returncode=64,
                    stdout=json.dumps(safety_v3_output),
                    stderr=""
                )

        mock_run.side_effect = side_effect

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Test via _run_vulnerability_scan which orchestrates fallbacks
        vulns = scanner._run_vulnerability_scan()

        assert len(vulns) == 1
        assert vulns[0]["name"] == "django"
        assert vulns[0]["version"] == "3.1.0"
        assert len(vulns[0]["vulns"]) == 1
        assert vulns[0]["vulns"][0]["id"] == "CVE-2023-12345"

    @patch("subprocess.run")
    def test_returns_empty_when_no_tools_available(self, mock_run, mock_graph_client, temp_repo):
        """Test graceful handling when no vulnerability scanning tools are installed."""
        mock_run.side_effect = FileNotFoundError("command not found")

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # Test via _run_vulnerability_scan which orchestrates fallbacks
        vulns = scanner._run_vulnerability_scan()

        assert vulns == []

    @patch("subprocess.run")
    def test_pip_audit_returns_none_when_not_installed(self, mock_run, mock_graph_client, temp_repo):
        """Test _run_pip_audit returns None when pip-audit is not installed."""
        mock_run.side_effect = FileNotFoundError("pip-audit not found")

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        result = scanner._run_pip_audit()

        assert result is None


class TestUvSecure:
    """Test uv-secure integration for uv.lock projects."""

    @pytest.fixture
    def temp_repo_with_uv_lock(self, tmp_path):
        """Create a temporary repository with uv.lock file."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        # Create uv.lock file
        uv_lock = repo_path / "uv.lock"
        uv_lock.write_text("version = 1\n[[package]]\nname = \"requests\"\nversion = \"2.25.0\"\n")

        return repo_path

    @patch("subprocess.run")
    def test_uv_secure_used_when_uv_lock_exists(self, mock_run, mock_graph_client, temp_repo_with_uv_lock):
        """Test uv-secure is used when uv.lock exists."""
        uv_secure_output = {
            "files": [
                {
                    "file_path": str(temp_repo_with_uv_lock / "uv.lock"),
                    "dependencies": [
                        {
                            "name": "requests",
                            "version": "2.25.0",
                            "direct": True,
                            "vulns": [
                                {
                                    "id": "GHSA-xxxx-yyyy-zzzz",
                                    "details": "Security vulnerability in requests",
                                    "fix_versions": ["2.31.0"],
                                    "aliases": ["CVE-2023-12345"],
                                    "link": "https://osv.dev/vulnerability/GHSA-xxxx-yyyy-zzzz"
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        mock_run.return_value = Mock(
            returncode=2,  # vulnerabilities found
            stdout=json.dumps(uv_secure_output),
            stderr=""
        )

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo_with_uv_lock)}
        )

        vulns = scanner._run_vulnerability_scan()

        # Verify uv-secure was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "uv-secure" in call_args

        # Verify results
        assert len(vulns) == 1
        assert vulns[0]["name"] == "requests"
        assert vulns[0]["version"] == "2.25.0"
        assert len(vulns[0]["vulns"]) == 1
        assert vulns[0]["vulns"][0]["id"] == "GHSA-xxxx-yyyy-zzzz"

    @patch("subprocess.run")
    def test_uv_secure_no_vulnerabilities(self, mock_run, mock_graph_client, temp_repo_with_uv_lock):
        """Test uv-secure with no vulnerabilities."""
        uv_secure_output = {
            "files": [
                {
                    "file_path": str(temp_repo_with_uv_lock / "uv.lock"),
                    "dependencies": [
                        {"name": "requests", "version": "2.31.0", "direct": True, "vulns": []}
                    ]
                }
            ]
        }

        mock_run.return_value = Mock(
            returncode=0,  # no vulnerabilities
            stdout=json.dumps(uv_secure_output),
            stderr=""
        )

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo_with_uv_lock)}
        )

        vulns = scanner._run_vulnerability_scan()

        assert vulns == []

    @patch("subprocess.run")
    def test_uv_secure_returns_none_when_not_installed(self, mock_run, mock_graph_client, temp_repo_with_uv_lock):
        """Test _run_uv_secure returns None when uv-secure is not installed."""
        mock_run.side_effect = FileNotFoundError("uv-secure not found")

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo_with_uv_lock)}
        )

        uv_lock_path = temp_repo_with_uv_lock / "uv.lock"
        result = scanner._run_uv_secure(uv_lock_path)

        assert result is None

    @patch("subprocess.run")
    def test_fallback_to_pip_audit_when_uv_secure_not_installed(self, mock_run, mock_graph_client, temp_repo_with_uv_lock):
        """Test fallback to pip-audit when uv-secure is not installed but uv.lock exists."""
        pip_audit_output = {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.25.0",
                    "vulns": [{"id": "PYSEC-2023-001", "fix_versions": ["2.31.0"]}]
                }
            ]
        }

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "uv-secure" in cmd:
                raise FileNotFoundError("uv-secure not found")
            elif "pip-audit" in cmd:
                return Mock(returncode=1, stdout=json.dumps(pip_audit_output), stderr="")

        mock_run.side_effect = side_effect

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo_with_uv_lock)}
        )

        vulns = scanner._run_vulnerability_scan()

        assert len(vulns) == 1
        assert vulns[0]["name"] == "requests"

    def test_convert_uv_secure_format(self, mock_graph_client, tmp_path):
        """Test conversion of uv-secure JSON to pip-audit format."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / "requirements.txt").write_text("requests==2.25.0\n")

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(repo_path)}
        )

        uv_secure_json = json.dumps({
            "files": [
                {
                    "file_path": "/path/to/uv.lock",
                    "dependencies": [
                        {
                            "name": "urllib3",
                            "version": "2.5.0",
                            "direct": False,
                            "vulns": [
                                {
                                    "id": "GHSA-gm62-xv2j-4w53",
                                    "details": "URL parsing issue",
                                    "fix_versions": ["2.6.0"],
                                    "aliases": [],
                                    "link": "https://osv.dev/vulnerability/GHSA-gm62-xv2j-4w53"
                                },
                                {
                                    "id": "GHSA-2xpw-w6gg-jr37",
                                    "details": "Another issue",
                                    "fix_versions": ["2.6.0"],
                                    "aliases": ["CVE-2024-1234"],
                                    "link": "https://osv.dev/vulnerability/GHSA-2xpw-w6gg-jr37"
                                }
                            ]
                        }
                    ]
                }
            ]
        })

        result = scanner._convert_uv_secure_to_pip_audit_format(uv_secure_json)

        assert len(result) == 1
        assert result[0]["name"] == "urllib3"
        assert result[0]["version"] == "2.5.0"
        assert len(result[0]["vulns"]) == 2
        assert result[0]["vulns"][0]["id"] == "GHSA-gm62-xv2j-4w53"
        assert result[0]["vulns"][1]["aliases"] == ["CVE-2024-1234"]


class TestIgnorePackages:
    """Test ignore_packages configuration (REPO-413)."""

    def test_init_with_ignore_packages(self, mock_graph_client, temp_repo):
        """Test initialization with ignore_packages config."""
        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "ignore_packages": ["setuptools", "pip", "wheel"]
            }
        )

        assert "setuptools" in scanner.ignore_packages
        assert "pip" in scanner.ignore_packages
        assert "wheel" in scanner.ignore_packages

    def test_ignored_package_returns_none(self, mock_graph_client, temp_repo):
        """Test that ignored packages return None from _create_finding."""
        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "ignore_packages": ["requests"]
            }
        )

        vuln_data = {
            "name": "requests",
            "version": "2.25.0",
            "vulns": [
                {
                    "id": "PYSEC-2023-74",
                    "description": "Security issue",
                    "fix_versions": ["2.31.0"],
                    "aliases": []
                }
            ]
        }

        finding = scanner._create_finding(vuln_data)

        assert finding is None

    def test_non_ignored_package_creates_finding(self, mock_graph_client, temp_repo):
        """Test that non-ignored packages still create findings."""
        mock_graph_client.execute_query.return_value = []

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "ignore_packages": ["setuptools"]
            }
        )

        vuln_data = {
            "name": "requests",
            "version": "2.25.0",
            "vulns": [
                {
                    "id": "PYSEC-2023-74",
                    "description": "Security issue",
                    "fix_versions": ["2.31.0"],
                    "aliases": []
                }
            ]
        }

        finding = scanner._create_finding(vuln_data)

        assert finding is not None
        assert "requests" in finding.title

    def test_ignore_case_insensitive(self, mock_graph_client, temp_repo):
        """Test that package name matching is case-insensitive."""
        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "ignore_packages": ["Django"]  # Upper case
            }
        )

        vuln_data = {
            "name": "django",  # Lower case
            "version": "3.1.0",
            "vulns": [{"id": "CVE-123", "description": "Test", "fix_versions": [], "aliases": []}]
        }

        finding = scanner._create_finding(vuln_data)

        assert finding is None


class TestOutdatedPackageDetection:
    """Test outdated package detection (REPO-413)."""

    def test_init_with_check_outdated(self, mock_graph_client, temp_repo):
        """Test initialization with check_outdated config."""
        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "check_outdated": True
            }
        )

        assert scanner.check_outdated is True

    @patch("subprocess.run")
    def test_check_outdated_packages(self, mock_run, mock_graph_client, temp_repo):
        """Test checking for outdated packages."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps([
                {"name": "requests", "version": "2.25.0", "latest_version": "2.31.0"},
                {"name": "django", "version": "2.0.0", "latest_version": "4.0.0"}
            ]),
            stderr=""
        )

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo), "check_outdated": True}
        )

        outdated = scanner._check_outdated_packages()

        assert len(outdated) == 2
        assert outdated[0]["name"] == "requests"
        assert outdated[1]["name"] == "django"

    def test_is_significantly_outdated(self, mock_graph_client, temp_repo):
        """Test major version difference detection."""
        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        # 2 major versions behind - should report
        assert scanner._is_significantly_outdated("2.0.0", "4.0.0") is True

        # Only 1 major version behind - should not report
        assert scanner._is_significantly_outdated("3.0.0", "4.0.0") is False

        # Same major version - should not report
        assert scanner._is_significantly_outdated("4.0.0", "4.5.0") is False

        # Invalid versions - should not report
        assert scanner._is_significantly_outdated("invalid", "4.0.0") is False

    def test_outdated_to_findings(self, mock_graph_client, temp_repo):
        """Test conversion of outdated packages to findings."""
        mock_graph_client.execute_query.return_value = [{"file_path": "app.py"}]

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={"repository_path": str(temp_repo)}
        )

        outdated = [
            {"name": "django", "version": "2.0.0", "latest_version": "4.0.0"},
            {"name": "requests", "version": "2.30.0", "latest_version": "2.31.0"}  # Only 1 minor behind
        ]

        findings = scanner._outdated_to_findings(outdated)

        # Only django should be reported (2 major versions behind)
        assert len(findings) == 1
        assert findings[0].title == "Outdated dependency: django 2.0.0 → 4.0.0"
        assert findings[0].severity == Severity.INFO
        assert findings[0].graph_context["type"] == "outdated"

    def test_outdated_respects_ignore_packages(self, mock_graph_client, temp_repo):
        """Test that outdated detection respects ignore_packages."""
        mock_graph_client.execute_query.return_value = []

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "ignore_packages": ["django"]
            }
        )

        outdated = [
            {"name": "django", "version": "2.0.0", "latest_version": "4.0.0"},
        ]

        findings = scanner._outdated_to_findings(outdated)

        # Django should be ignored
        assert len(findings) == 0

    @patch.object(DependencyScanner, "_run_pip_audit")
    @patch.object(DependencyScanner, "_check_outdated_packages")
    def test_detect_includes_outdated_when_enabled(
        self, mock_outdated, mock_audit, mock_graph_client, temp_repo
    ):
        """Test that detect() includes outdated packages when check_outdated is True."""
        mock_audit.return_value = []
        mock_outdated.return_value = [
            {"name": "django", "version": "2.0.0", "latest_version": "4.0.0"}
        ]
        mock_graph_client.execute_query.return_value = []

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "check_outdated": True
            }
        )

        findings = scanner.detect()

        assert len(findings) == 1
        assert "Outdated dependency" in findings[0].title
        mock_outdated.assert_called_once()

    @patch.object(DependencyScanner, "_run_pip_audit")
    @patch.object(DependencyScanner, "_check_outdated_packages")
    def test_detect_skips_outdated_when_disabled(
        self, mock_outdated, mock_audit, mock_graph_client, temp_repo
    ):
        """Test that detect() skips outdated check when check_outdated is False."""
        mock_audit.return_value = []

        scanner = DependencyScanner(
            mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "check_outdated": False
            }
        )

        findings = scanner.detect()

        assert len(findings) == 0
        mock_outdated.assert_not_called()
