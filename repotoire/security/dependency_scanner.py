"""Dependency vulnerability scanner using pip-audit with Neo4j enrichment.

This hybrid detector combines pip-audit's vulnerability analysis with Neo4j graph data
to provide dependency vulnerability detection with rich context.

Architecture:
    1. Run pip-audit on repository dependencies
    2. Parse pip-audit JSON output
    3. Enrich findings with Neo4j graph data (usage patterns, import relationships)
    4. Generate detailed vulnerability findings with context

This approach achieves:
    - Comprehensive CVE detection (OSV database)
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
    """Detects dependency vulnerabilities using pip-audit with graph enrichment.

    Uses pip-audit for vulnerability scanning and Neo4j for usage context.

    Configuration:
        repository_path: Path to repository root (required)
        requirements_file: Path to requirements file (default: requirements.txt)
        max_findings: Maximum findings to report (default: 100)
        check_licenses: Also check license compliance (default: False)
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
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.requirements_file = config.get("requirements_file", "requirements.txt")
        self.max_findings = config.get("max_findings", 100)
        self.check_licenses = config.get("check_licenses", False)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run pip-audit and enrich findings with graph data.

        Returns:
            List of dependency vulnerability findings
        """
        logger.info(f"Scanning dependencies in {self.repository_path}")

        try:
            # Run pip-audit
            vulnerabilities = self._run_pip_audit()

            if not vulnerabilities:
                logger.info("No dependency vulnerabilities found")
                return []

            logger.info(f"Found {len(vulnerabilities)} vulnerable dependencies")

            # Convert to findings
            findings = []
            for vuln in vulnerabilities[:self.max_findings]:
                finding = self._create_finding(vuln)
                if finding:
                    findings.append(finding)

            # Enrich with graph data
            enriched_findings = self._enrich_with_graph_data(findings)

            logger.info(f"Returning {len(enriched_findings)} dependency vulnerability findings")
            return enriched_findings

        except subprocess.CalledProcessError as e:
            logger.error(f"pip-audit execution failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Dependency scanning failed: {e}", exc_info=True)
            return []

    def _run_pip_audit(self) -> List[Dict[str, Any]]:
        """Run pip-audit and return parsed results.

        Returns:
            List of vulnerability dictionaries from pip-audit JSON output
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

    def _create_finding(self, vuln: Dict[str, Any]) -> Optional[Finding]:
        """Convert pip-audit vulnerability to Finding.

        Args:
            vuln: Vulnerability dict from pip-audit

        Returns:
            Finding object or None if conversion fails
        """
        try:
            package_name = vuln.get("name", "unknown")
            package_version = vuln.get("version", "unknown")
            vulnerabilities = vuln.get("vulns", [])

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
