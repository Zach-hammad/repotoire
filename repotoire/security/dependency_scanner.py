"""Dependency vulnerability scanner using uv-secure/pip-audit with Neo4j enrichment.

This hybrid detector combines vulnerability analysis with Neo4j graph data
to provide dependency vulnerability detection with rich context.

Architecture:
    1. Try uv-secure first (for uv.lock projects - fastest)
    2. Fall back to pip-audit (for requirements.txt projects)
    3. Fall back to safety (if neither is available)
    4. Parse JSON output and enrich with Neo4j graph data
    5. Generate detailed vulnerability findings with context

This approach achieves:
    - Fast scanning via uv-secure for uv-based projects
    - Comprehensive CVE detection (OSV/PyPI Advisory Database)
    - License compliance checking
    - Rich context (which files use vulnerable dependencies)
    - Actionable remediation recommendations
"""

import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import Neo4jClient
from repotoire.models import Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class DependencyScanner(CodeSmellDetector):
    """Detects dependency vulnerabilities using uv-secure/pip-audit with graph enrichment.

    Scanning order:
        1. uv-secure (if uv.lock exists) - fastest, native to uv projects
        2. pip-audit (if requirements.txt exists) - mature, PyPA-maintained
        3. safety (fallback) - works with any pip environment

    Configuration:
        repository_path: Path to repository root (required)
        requirements_file: Path to requirements file (default: requirements.txt)
        max_findings: Maximum findings to report (default: 100)
        check_licenses: Also check license compliance (default: False)
        ignore_packages: List of package names to skip
        check_outdated: Check for significantly outdated packages
    """

    # Severity mapping: CVSS score to our severity levels
    # https://www.first.org/cvss/specification-document
    CVSS_SEVERITY_MAP = {
        "CRITICAL": Severity.CRITICAL,  # 9.0-10.0
        "HIGH": Severity.HIGH,          # 7.0-8.9
        "MEDIUM": Severity.MEDIUM,      # 4.0-6.9
        "LOW": Severity.LOW,            # 0.1-3.9
    }

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict] = None):
        """Initialize dependency scanner.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - requirements_file: Path to requirements file
                - max_findings: Max findings to report
                - check_licenses: Enable license checking
                - ignore_packages: List of package names to ignore (e.g., ["setuptools"])
                - check_outdated: Check for significantly outdated packages
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.requirements_file = config.get("requirements_file", "requirements.txt")
        self.max_findings = config.get("max_findings", 100)
        self.check_licenses = config.get("check_licenses", False)
        # REPO-413: Add ignore list for packages to skip
        self.ignore_packages = {pkg.lower() for pkg in config.get("ignore_packages", [])}
        # REPO-413: Optional outdated package detection
        self.check_outdated = config.get("check_outdated", False)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run vulnerability scan and enrich findings with graph data.

        Scanning order:
            1. uv-secure (if uv.lock exists)
            2. pip-audit (if requirements.txt exists)
            3. safety (fallback)

        Returns:
            List of dependency vulnerability findings
        """
        logger.info(f"Scanning dependencies in {self.repository_path}")

        try:
            findings = []

            # Try uv-secure first (for uv.lock projects), then pip-audit, then safety
            vulnerabilities = self._run_vulnerability_scan()

            if vulnerabilities:
                logger.info(f"Found {len(vulnerabilities)} vulnerable dependencies")

                # Convert to findings
                for vuln in vulnerabilities[:self.max_findings]:
                    finding = self._create_finding(vuln)
                    if finding:
                        findings.append(finding)
            else:
                logger.info("No dependency vulnerabilities found")

            # REPO-413: Optionally check for outdated packages
            if self.check_outdated:
                outdated = self._check_outdated_packages()
                if outdated:
                    logger.info(f"Found {len(outdated)} outdated packages")
                    outdated_findings = self._outdated_to_findings(outdated)
                    findings.extend(outdated_findings[:self.max_findings - len(findings)])

            # Enrich with graph data
            enriched_findings = self._enrich_with_graph_data(findings)

            logger.info(f"Returning {len(enriched_findings)} dependency findings")
            return enriched_findings

        except subprocess.CalledProcessError as e:
            logger.error(f"pip-audit execution failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Dependency scanning failed: {e}", exc_info=True)
            return []

    def _run_vulnerability_scan(self) -> List[Dict[str, Any]]:
        """Run vulnerability scan using available tools in priority order.

        Scanning order:
            1. uv-secure (if uv.lock exists) - fastest, native to uv projects
            2. pip-audit (if requirements.txt exists) - mature, PyPA-maintained
            3. safety (fallback) - works with any pip environment

        Returns:
            List of vulnerability dictionaries in pip-audit format
        """
        # Check for uv.lock first (uv-based projects)
        uv_lock_path = self.repository_path / "uv.lock"
        if uv_lock_path.exists():
            logger.info("Found uv.lock, using uv-secure for scanning")
            vulnerabilities = self._run_uv_secure(uv_lock_path)
            if vulnerabilities is not None:  # None means tool not found
                return vulnerabilities
            logger.warning("uv-secure not available, falling back to pip-audit")

        # Try pip-audit
        vulnerabilities = self._run_pip_audit()
        if vulnerabilities is not None:  # None means tool not found
            return vulnerabilities

        # Fall back to safety
        logger.warning("pip-audit not available, falling back to safety")
        return self._run_safety_fallback()

    def _run_uv_secure(self, uv_lock_path: Path) -> Optional[List[Dict[str, Any]]]:
        """Run uv-secure on uv.lock and return parsed results.

        Args:
            uv_lock_path: Path to uv.lock file

        Returns:
            List of vulnerability dictionaries in pip-audit format,
            or None if uv-secure is not installed
        """
        try:
            cmd = ["uv-secure", "--format", "json", str(uv_lock_path)]
            logger.debug(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.repository_path,
                timeout=120,  # 2 minute timeout
            )

            # uv-secure returns 0 for clean, 2 for vulnerabilities found
            if result.returncode not in [0, 2]:
                logger.error(f"uv-secure failed with code {result.returncode}: {result.stderr}")
                return []

            if not result.stdout:
                return []

            try:
                return self._convert_uv_secure_to_pip_audit_format(result.stdout)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse uv-secure JSON: {e}")
                return []

        except FileNotFoundError:
            # uv-secure not installed
            return None
        except subprocess.TimeoutExpired:
            logger.warning("uv-secure timed out")
            return []

    def _convert_uv_secure_to_pip_audit_format(self, uv_secure_output: str) -> List[Dict[str, Any]]:
        """Convert uv-secure JSON output to pip-audit format.

        Args:
            uv_secure_output: Raw JSON output from uv-secure

        Returns:
            List of vulnerability dictionaries in pip-audit format
        """
        try:
            data = json.loads(uv_secure_output)
            result = []

            # uv-secure format: {"files": [{"file_path": "...", "dependencies": [...]}]}
            for file_entry in data.get("files", []):
                for dep in file_entry.get("dependencies", []):
                    vulns = dep.get("vulns", [])
                    if vulns:
                        # Convert to pip-audit format
                        result.append({
                            "name": dep.get("name", "unknown"),
                            "version": dep.get("version", "unknown"),
                            "vulns": [
                                {
                                    "id": v.get("id", "UNKNOWN"),
                                    "description": v.get("details", ""),
                                    "fix_versions": v.get("fix_versions", []),
                                    "aliases": v.get("aliases", []),
                                    "link": v.get("link", ""),
                                }
                                for v in vulns
                            ],
                        })

            return result

        except Exception as e:
            logger.error(f"Failed to convert uv-secure output: {e}")
            return []

    def _run_pip_audit(self) -> Optional[List[Dict[str, Any]]]:
        """Run pip-audit and return parsed results.

        Returns:
            List of vulnerability dictionaries from pip-audit JSON output,
            or None if pip-audit is not installed
        """
        cmd = ["pip-audit", "--format", "json", "--progress-spinner", "off"]

        # Add requirements file if specified
        req_path = self.repository_path / self.requirements_file
        if req_path.exists():
            cmd.extend(["--requirement", str(req_path)])
        else:
            # Scan current environment if no requirements file
            logger.warning(f"Requirements file not found: {req_path}, scanning environment")

        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.repository_path,
                timeout=300,  # 5 minute timeout
            )

            # pip-audit returns non-zero if vulnerabilities found
            if result.returncode not in [0, 1]:
                logger.error(f"pip-audit failed with code {result.returncode}: {result.stderr}")
                return []

            if not result.stdout:
                return []

            try:
                output = json.loads(result.stdout)
                # pip-audit JSON format: {"dependencies": [...]}
                return output.get("dependencies", [])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse pip-audit JSON: {e}")
                return []

        except FileNotFoundError:
            # pip-audit not installed
            return None

    def _run_safety_fallback(self) -> List[Dict[str, Any]]:
        """Run safety check as fallback when pip-audit is not available.

        Returns:
            List of vulnerability dictionaries in pip-audit format
        """
        try:
            cmd = ["safety", "check", "--json"]

            # Add requirements file if specified
            req_path = self.repository_path / self.requirements_file
            if req_path.exists():
                cmd.extend(["--file", str(req_path)])

            logger.debug(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.repository_path,
                timeout=120,
            )

            # safety returns 64 for vulnerabilities found, 0 for clean
            if result.returncode not in [0, 64]:
                logger.error(f"safety failed with code {result.returncode}: {result.stderr}")
                return []

            if not result.stdout:
                return []

            try:
                # Safety JSON format is different from pip-audit
                # Convert to pip-audit format
                return self._convert_safety_to_pip_audit_format(result.stdout)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse safety JSON: {e}")
                return []

        except FileNotFoundError:
            logger.warning("Neither pip-audit nor safety installed, skipping dependency scan")
            return []

    def _convert_safety_to_pip_audit_format(self, safety_output: str) -> List[Dict[str, Any]]:
        """Convert safety JSON output to pip-audit format.

        Args:
            safety_output: Raw JSON output from safety check

        Returns:
            List of vulnerability dictionaries in pip-audit format
        """
        try:
            data = json.loads(safety_output)

            # Safety 2.x format: list of vulnerability tuples
            # [package_name, affected_version, installed_version, description, advisory_id]
            if isinstance(data, list):
                # Group vulnerabilities by package
                packages: Dict[str, Dict[str, Any]] = {}
                for vuln in data:
                    if isinstance(vuln, (list, tuple)) and len(vuln) >= 5:
                        pkg_name = vuln[0]
                        installed_version = vuln[2]
                        description = vuln[3]
                        advisory_id = str(vuln[4])

                        if pkg_name not in packages:
                            packages[pkg_name] = {
                                "name": pkg_name,
                                "version": installed_version,
                                "vulns": []
                            }

                        packages[pkg_name]["vulns"].append({
                            "id": advisory_id,
                            "description": description,
                            "fix_versions": [],
                            "aliases": []
                        })

                return list(packages.values())

            # Safety 3.x format: {"report_meta": {...}, "vulnerabilities": [...]}
            elif isinstance(data, dict) and "vulnerabilities" in data:
                packages: Dict[str, Dict[str, Any]] = {}
                for vuln in data.get("vulnerabilities", []):
                    pkg_name = vuln.get("package_name", "unknown")
                    installed_version = vuln.get("installed_version", "unknown")
                    advisory_id = vuln.get("vulnerability_id", "UNKNOWN")
                    description = vuln.get("advisory", "")

                    if pkg_name not in packages:
                        packages[pkg_name] = {
                            "name": pkg_name,
                            "version": installed_version,
                            "vulns": []
                        }

                    packages[pkg_name]["vulns"].append({
                        "id": advisory_id,
                        "description": description,
                        "fix_versions": vuln.get("fixed_versions", []),
                        "aliases": vuln.get("cve", []) if isinstance(vuln.get("cve"), list) else []
                    })

                return list(packages.values())

            return []

        except Exception as e:
            logger.error(f"Failed to convert safety output: {e}")
            return []

    def _create_finding(self, vuln: Dict[str, Any]) -> Optional[Finding]:
        """Convert pip-audit vulnerability to Finding.

        Args:
            vuln: Vulnerability dict from pip-audit

        Returns:
            Finding object or None if conversion fails or package is ignored
        """
        try:
            package_name = vuln.get("name", "unknown")
            package_version = vuln.get("version", "unknown")
            vulnerabilities = vuln.get("vulns", [])

            # REPO-413: Skip ignored packages
            if package_name.lower() in self.ignore_packages:
                logger.debug(f"Skipping ignored package: {package_name}")
                return None

            if not vulnerabilities:
                return None

            # Get the most severe vulnerability
            most_severe = max(
                vulnerabilities,
                key=lambda v: self._cvss_to_score(v.get("fix_versions", [])),
                default=vulnerabilities[0]
            )

            vuln_id = most_severe.get("id", "UNKNOWN")
            description = most_severe.get("description", "No description available")
            fix_versions = most_severe.get("fix_versions", [])
            aliases = most_severe.get("aliases", [])

            # Determine severity from CVSS or description
            severity = self._determine_severity(most_severe)

            # Create title
            title = f"Vulnerable dependency: {package_name} {package_version}"
            if vuln_id:
                title = f"{title} ({vuln_id})"

            # Create detailed description
            detailed_desc = f"""Package: {package_name} {package_version}
Vulnerability: {vuln_id}
{description}

Fix: Upgrade to {', '.join(fix_versions) if fix_versions else 'no fix available'}
"""
            if aliases:
                detailed_desc += f"\nAliases: {', '.join(aliases)}"

            # Find files that import this dependency
            affected_files = self._find_files_using_package(package_name)

            finding = Finding(
                id=f"dep-vuln-{package_name}-{vuln_id}",
                title=title,
                description=detailed_desc,
                severity=severity,
                detector="dependency_scanner",
                affected_nodes=[],  # Dependency vulnerabilities don't have specific nodes
                affected_files=affected_files[:20],  # Limit to 20 files
                graph_context={
                    "package": package_name,
                    "version": package_version,
                    "vulnerability_id": vuln_id,
                    "fix_versions": fix_versions,
                    "aliases": aliases,
                    "cves": [a for a in aliases if a.startswith("CVE-")],
                },
            )

            return finding

        except Exception as e:
            logger.error(f"Failed to create finding: {e}")
            return None

    def _determine_severity(self, vuln: Dict[str, Any]) -> Severity:
        """Determine severity from vulnerability data.

        Args:
            vuln: Vulnerability dict

        Returns:
            Severity level
        """
        # Try to get severity from pip-audit (if available)
        severity_str = vuln.get("severity", "").upper()
        if severity_str in self.CVSS_SEVERITY_MAP:
            return self.CVSS_SEVERITY_MAP[severity_str]

        # Fallback: check for critical keywords
        description = vuln.get("description", "").lower()
        if any(word in description for word in ["remote code execution", "rce", "critical"]):
            return Severity.CRITICAL
        elif any(word in description for word in ["sql injection", "xss", "csrf", "high"]):
            return Severity.HIGH
        elif "medium" in description:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _cvss_to_score(self, fix_versions: List[str]) -> float:
        """Convert to numeric score for comparison (lower is worse).

        Args:
            fix_versions: List of fix versions

        Returns:
            Numeric score (lower = more severe)
        """
        # If no fix available, it's more severe
        if not fix_versions:
            return 0.0
        return len(fix_versions)

    def _find_files_using_package(self, package_name: str) -> List[str]:
        """Find Python files that import the vulnerable package.

        Args:
            package_name: Package name to search for

        Returns:
            List of file paths that import the package
        """
        try:
            # Normalize package name (e.g., "Django" -> "django")
            normalized_name = package_name.lower().replace("-", "_")

            query = """
            MATCH (f:File)-[:IMPORTS]->(m:Module)
            WHERE toLower(m.name) CONTAINS $package_name
            RETURN DISTINCT f.path as file_path
            LIMIT 100
            """

            results = self.db.execute_query(
                query,
                parameters={"package_name": normalized_name}
            )

            return [record["file_path"] for record in results]

        except Exception as e:
            logger.warning(f"Failed to find files using {package_name}: {e}")
            return []

    def _enrich_with_graph_data(self, findings: List[Finding]) -> List[Finding]:
        """Enrich findings with graph metadata.

        Args:
            findings: List of findings to enrich

        Returns:
            Enriched findings
        """
        for finding in findings:
            try:
                # Add import count to graph_context
                package_name = finding.graph_context.get("package", "")
                if package_name and finding.affected_files:
                    finding.graph_context["import_count"] = len(finding.affected_files)
                    finding.graph_context["affected_file_count"] = len(finding.affected_files)

            except Exception as e:
                logger.warning(f"Failed to enrich finding {finding.id}: {e}")
                continue

        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a dependency finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity

    def _check_outdated_packages(self) -> List[Dict[str, Any]]:
        """Check for significantly outdated packages using pip list --outdated.

        REPO-413: Optional feature to detect outdated packages that may have
        security implications even without known CVEs.

        Returns:
            List of outdated package dictionaries
        """
        try:
            result = subprocess.run(
                ["pip", "list", "--outdated", "--format=json"],
                capture_output=True,
                text=True,
                cwd=self.repository_path,
                timeout=60,
            )

            if result.returncode != 0:
                logger.warning(f"pip list --outdated failed: {result.stderr}")
                return []

            if not result.stdout:
                return []

            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse pip list JSON: {e}")
                return []

        except FileNotFoundError:
            logger.warning("pip not found, skipping outdated check")
            return []
        except subprocess.TimeoutExpired:
            logger.warning("pip list --outdated timed out")
            return []

    def _outdated_to_findings(self, outdated: List[Dict[str, Any]]) -> List[Finding]:
        """Convert outdated packages to findings.

        REPO-413: Creates INFO-severity findings for significantly outdated packages.
        Only reports packages with major version differences.

        Args:
            outdated: List of outdated package dicts from pip list --outdated

        Returns:
            List of findings for outdated packages
        """
        findings = []

        for pkg in outdated:
            package_name = pkg.get("name", "unknown")
            current_version = pkg.get("version", "unknown")
            latest_version = pkg.get("latest_version", "unknown")

            # REPO-413: Skip ignored packages
            if package_name.lower() in self.ignore_packages:
                logger.debug(f"Skipping ignored outdated package: {package_name}")
                continue

            # Only report if there's a major version difference
            if not self._is_significantly_outdated(current_version, latest_version):
                continue

            # Find files using this package
            affected_files = self._find_files_using_package(package_name)

            finding = Finding(
                id=f"dep-outdated-{package_name}",
                title=f"Outdated dependency: {package_name} {current_version} → {latest_version}",
                description=f"""Package: {package_name}
Current version: {current_version}
Latest version: {latest_version}

This package is significantly outdated. While no specific vulnerabilities are known,
outdated packages may miss important security patches and bug fixes.

Recommendation: Update to the latest version using:
  pip install --upgrade {package_name}
""",
                severity=Severity.INFO,
                detector="dependency_scanner",
                affected_nodes=[],
                affected_files=affected_files[:20],
                graph_context={
                    "package": package_name,
                    "current_version": current_version,
                    "latest_version": latest_version,
                    "type": "outdated",
                },
            )

            findings.append(finding)

        return findings

    def _is_significantly_outdated(self, current: str, latest: str) -> bool:
        """Check if package is significantly outdated (major version difference).

        Args:
            current: Current installed version
            latest: Latest available version

        Returns:
            True if there's a major version difference
        """
        try:
            # Extract major version numbers
            current_major = int(current.split(".")[0])
            latest_major = int(latest.split(".")[0])

            # Report if major version is at least 2 behind
            return latest_major - current_major >= 2
        except (ValueError, IndexError):
            # Can't parse version, skip
            return False
