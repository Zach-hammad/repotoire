"""npm/bun audit security detector with FalkorDB graph enrichment.

This hybrid detector uses npm audit (or bun audit) to detect known vulnerabilities
in JavaScript/TypeScript dependencies, similar to how BanditDetector works for Python.

Architecture:
    1. Run npm audit --json (or bun audit) on repository
    2. Parse audit JSON output
    3. Enrich findings with FalkorDB graph data
    4. Generate detailed findings with context

This approach achieves:
    - Detection of known CVEs in dependencies
    - Security severity classification (critical, high, medium, low)
    - Rich context (graph-based metadata)
    - Actionable fix suggestions with upgrade paths
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.external_tool_runner import (
    get_graph_context,
    get_js_runtime,
    run_external_tool,
)
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


class NpmAuditDetector(CodeSmellDetector):
    """Detects security vulnerabilities in npm dependencies with graph enrichment.

    Uses npm audit (or bun audit if available) for vulnerability detection
    and FalkorDB for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        max_findings: Maximum findings to report (default: 100)
        min_severity: Minimum severity to report (default: "low")
        production_only: Only check production dependencies (default: False)
    """

    # npm audit severity to Repotoire severity mapping
    SEVERITY_MAP = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "moderate": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
    }

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize npm audit detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - max_findings: Max findings to report (default: 100)
                - min_severity: Minimum severity to report (default: "low")
                - production_only: Only check production deps (default: False)
            enricher: Optional GraphEnricher for persistent collaboration
        """
        super().__init__(graph_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)
        self.min_severity = config.get("min_severity", "low")
        self.production_only = config.get("production_only", False)
        self.enricher = enricher

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run npm audit and enrich findings with graph data.

        Returns:
            List of security vulnerability findings
        """
        # Check if package.json exists
        package_json = self.repository_path / "package.json"
        if not package_json.exists():
            logger.info("No package.json found, skipping npm audit")
            return []

        logger.info(f"Running security audit on {self.repository_path}")

        # Run audit and get results
        vulnerabilities = self._run_audit()

        if not vulnerabilities:
            logger.info("No security vulnerabilities found")
            return []

        # Filter by severity
        severity_order = ["critical", "high", "moderate", "low", "info"]
        min_idx = severity_order.index(self.min_severity) if self.min_severity in severity_order else 3
        filtered = [v for v in vulnerabilities if severity_order.index(v.get("severity", "info")) <= min_idx]

        # Create findings
        findings = []
        for vuln in filtered[:self.max_findings]:
            finding = self._create_finding(vuln)
            if finding:
                findings.append(finding)

        logger.info(f"Created {len(findings)} security findings")
        return findings

    def _run_audit(self) -> List[Dict[str, Any]]:
        """Run npm/bun audit and parse JSON output.

        Returns:
            List of vulnerability dictionaries
        """
        # Check for lock file - npm audit requires package-lock.json
        has_package_lock = (self.repository_path / "package-lock.json").exists()
        has_yarn_lock = (self.repository_path / "yarn.lock").exists()
        has_pnpm_lock = (self.repository_path / "pnpm-lock.yaml").exists()
        has_bun_lock = (self.repository_path / "bun.lockb").exists()

        if not any([has_package_lock, has_yarn_lock, has_pnpm_lock, has_bun_lock]):
            logger.warning(
                "No lock file found (package-lock.json, yarn.lock, pnpm-lock.yaml, or bun.lockb). "
                "npm audit requires a lock file. Run 'npm install' first."
            )
            return []

        runtime = get_js_runtime()

        # Use appropriate audit command based on lock file and runtime
        if has_yarn_lock:
            cmd = ["yarn", "audit", "--json"]
        elif has_pnpm_lock:
            cmd = ["pnpm", "audit", "--json"]
        elif has_bun_lock and runtime == "bun":
            # Bun has audit support now
            cmd = ["bun", "audit", "--json"]
        else:
            # Default to npm audit
            cmd = ["npm", "audit", "--json"]

        if self.production_only:
            if cmd[0] == "npm":
                cmd.append("--omit=dev")
            elif cmd[0] == "yarn":
                cmd.append("--groups=production")
            elif cmd[0] == "pnpm":
                cmd.append("--prod")

        result = run_external_tool(
            cmd=cmd,
            tool_name="npm audit",
            timeout=120,
            cwd=self.repository_path,
        )

        # npm audit returns non-zero exit code when vulnerabilities found
        # but still outputs valid JSON
        if not result.stdout:
            return []

        try:
            audit_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse npm audit JSON: {e}")
            return []

        # Parse npm audit v2 format (npm 7+)
        vulnerabilities = []

        if "vulnerabilities" in audit_data:
            # npm v7+ format
            for pkg_name, vuln_data in audit_data.get("vulnerabilities", {}).items():
                severity = vuln_data.get("severity", "info")
                via = vuln_data.get("via", [])

                # via can contain strings (advisory IDs) or objects (advisory details)
                advisories = []
                for v in via:
                    if isinstance(v, dict):
                        advisories.append(v)
                    elif isinstance(v, str):
                        # Reference to another package
                        advisories.append({"name": v, "title": f"Via {v}"})

                for advisory in advisories:
                    if isinstance(advisory, dict) and "title" in advisory:
                        vulnerabilities.append({
                            "package": pkg_name,
                            "severity": severity,
                            "title": advisory.get("title", "Unknown vulnerability"),
                            "url": advisory.get("url", ""),
                            "cwe": advisory.get("cwe", []),
                            "cvss": advisory.get("cvss", {}),
                            "range": advisory.get("range", vuln_data.get("range", "*")),
                            "fix_available": vuln_data.get("fixAvailable", False),
                        })
        elif "advisories" in audit_data:
            # npm v6 format
            for advisory_id, advisory in audit_data.get("advisories", {}).items():
                vulnerabilities.append({
                    "package": advisory.get("module_name", "unknown"),
                    "severity": advisory.get("severity", "info"),
                    "title": advisory.get("title", "Unknown vulnerability"),
                    "url": advisory.get("url", ""),
                    "cwe": advisory.get("cwe", ""),
                    "cvss": {},
                    "range": advisory.get("vulnerable_versions", "*"),
                    "fix_available": advisory.get("patched_versions", "") != "<0.0.0",
                })

        logger.info(f"npm audit found {len(vulnerabilities)} vulnerabilities")
        return vulnerabilities

    def _create_finding(self, vuln: Dict[str, Any]) -> Optional[Finding]:
        """Create finding from vulnerability with graph enrichment.

        Args:
            vuln: Vulnerability dictionary

        Returns:
            Finding object or None
        """
        package = vuln.get("package", "unknown")
        title = vuln.get("title", "Security vulnerability")
        severity_str = vuln.get("severity", "info")
        url = vuln.get("url", "")

        # Map severity
        severity = self.SEVERITY_MAP.get(severity_str, Severity.MEDIUM)

        # Try to find files that import this package
        affected_files = self._find_importing_files(package)

        # Enrich with graph data
        graph_data = {}
        if affected_files:
            graph_data = self._get_graph_context(affected_files[0], None)

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="NpmAuditDetector",
            severity=severity,
            title=f"Vulnerable dependency: {package}",
            description=self._build_description(vuln, affected_files),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=affected_files or ["package.json"],
            graph_context={
                "package": package,
                "vulnerability": title,
                "severity": severity_str,
                "url": url,
                "cwe": vuln.get("cwe", []),
                "range": vuln.get("range", "*"),
                "fix_available": vuln.get("fix_available", False),
                **graph_data,
            },
            suggested_fix=self._suggest_fix(vuln),
            estimated_effort="Small (15-30 minutes)" if vuln.get("fix_available") else "Medium (1-2 hours)",
            created_at=datetime.now(),
            language=self._detect_language(affected_files),
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_data.get("nodes"):
            for node in graph_data["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="NpmAuditDetector",
                        severity=severity.value,
                        issues=[f"CVE:{package}"],
                        confidence=0.95,
                        metadata={
                            "package": package,
                            "title": title,
                            "url": url,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="NpmAuditDetector",
                confidence=0.95,  # npm audit is authoritative
                evidence=["npm_audit", "external_tool", f"severity:{severity_str}"],
                tags=["security", "dependency", "npm_audit"],
            )
        )

        return finding

    def _find_importing_files(self, package: str) -> List[str]:
        """Find files that import the vulnerable package.

        Args:
            package: Package name

        Returns:
            List of file paths that import the package
        """
        # FalkorDB: Use ENDS WITH for string suffix matching instead of CONTAINS
        # (CONTAINS is for list membership in FalkorDB, not string substring)
        query = """
        MATCH (f:File)-[:CONTAINS]->(e)-[r:IMPORTS]->(target)
        WHERE target.qualifiedName ENDS WITH $package
           OR r.module ENDS WITH $package
           OR target.qualifiedName = $package
           OR r.module = $package
        RETURN DISTINCT f.filePath as file_path
        LIMIT 10
        """

        try:
            results = self.db.execute_query(query, {"package": package})
            return [r["file_path"] for r in results if r.get("file_path")]
        except Exception as e:
            logger.debug(f"Could not find importing files for {package}: {e}")
            return []

    def _get_graph_context(self, file_path: str, line: Optional[int]) -> Dict[str, Any]:
        """Get context from FalkorDB graph.

        Args:
            file_path: Relative file path
            line: Line number (optional)

        Returns:
            Dictionary with graph context
        """
        context = get_graph_context(self.db, file_path, line)

        return {
            "file_loc": context.get("file_loc", 0),
            "language": context.get("language", "javascript"),
            "nodes": context.get("affected_nodes", []),
            "complexity": max(context.get("complexities", [0]) or [0]),
        }

    def _build_description(
        self,
        vuln: Dict[str, Any],
        affected_files: List[str],
    ) -> str:
        """Build detailed description with context.

        Args:
            vuln: Vulnerability data
            affected_files: List of files importing the package

        Returns:
            Formatted description
        """
        package = vuln.get("package", "unknown")
        title = vuln.get("title", "Security vulnerability")
        severity = vuln.get("severity", "info")
        url = vuln.get("url", "")
        cwe = vuln.get("cwe", [])
        vulnerable_range = vuln.get("range", "*")

        desc = f"**{title}**\n\n"
        desc += f"**Package**: {package}\n"
        desc += f"**Severity**: {severity.upper()}\n"
        desc += f"**Vulnerable versions**: {vulnerable_range}\n"

        if url:
            desc += f"**Advisory**: {url}\n"

        if cwe:
            cwe_list = cwe if isinstance(cwe, list) else [cwe]
            desc += f"**CWE**: {', '.join(str(c) for c in cwe_list)}\n"

        if affected_files:
            desc += f"\n**Affected files** ({len(affected_files)}):\n"
            for f in affected_files[:5]:
                desc += f"  - {f}\n"
            if len(affected_files) > 5:
                desc += f"  - ... and {len(affected_files) - 5} more\n"

        return desc

    def _suggest_fix(self, vuln: Dict[str, Any]) -> str:
        """Suggest fix based on vulnerability data.

        Args:
            vuln: Vulnerability data

        Returns:
            Fix suggestion
        """
        package = vuln.get("package", "unknown")
        fix_available = vuln.get("fix_available", False)

        if fix_available:
            return f"Run `npm audit fix` or manually update {package} to a patched version"

        return f"Check for alternative packages or apply workarounds for {package}. See advisory for details."

    def _detect_language(self, affected_files: List[str]) -> str:
        """Detect language from affected files.

        Args:
            affected_files: List of affected file paths

        Returns:
            "typescript" or "javascript"
        """
        # Check if any affected files are TypeScript
        for f in affected_files:
            if f.endswith((".ts", ".tsx", ".mts", ".cts")):
                return "typescript"

        # Check if tsconfig.json exists in the repository
        if (self.repository_path / "tsconfig.json").exists():
            return "typescript"

        return "javascript"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for an npm audit finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
