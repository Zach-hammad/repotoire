"""Semgrep-based advanced security detector with Neo4j graph enrichment.

This hybrid detector combines Semgrep's powerful pattern matching with Neo4j graph data
to provide comprehensive security analysis with rich context.

Architecture:
    1. Run Semgrep on repository (advanced pattern-based security scanning)
    2. Parse Semgrep JSON output
    3. Enrich findings with Neo4j graph data (dependencies, data flow)
    4. Generate detailed security findings

This approach achieves:
    - Comprehensive coverage (OWASP Top 10, language-specific vulnerabilities)
    - Low false positives (semantic pattern matching)
    - Fast analysis (parallel execution, incremental scanning)
    - Rich context (graph-based impact analysis)

Performance: ~5-15 seconds even on large codebases
"""

import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import Neo4jClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class SemgrepDetector(CodeSmellDetector):
    """Detects security vulnerabilities using Semgrep with graph enrichment.

    Uses Semgrep for advanced security pattern detection and Neo4j for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        config: Semgrep ruleset (default: "auto" - OWASP Top 10 + language-specific)
        max_findings: Maximum findings to report (default: 50)
        severity_threshold: Minimum severity (ERROR, WARNING, INFO)
        exclude: List of patterns to exclude (default: tests, migrations)
    """

    # Semgrep severity to Repotoire severity mapping
    SEVERITY_MAP = {
        "ERROR": Severity.HIGH,
        "WARNING": Severity.MEDIUM,
        "INFO": Severity.LOW,
    }

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict] = None, enricher: Optional[GraphEnricher] = None):
        """Initialize Semgrep detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - config: Semgrep config/ruleset (default: "auto")
                - max_findings: Max findings to report
                - severity_threshold: Min severity level
                - exclude: List of patterns to exclude
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))

        # Semgrep config options:
        # - "auto": Auto-detect language and use recommended rules (OWASP Top 10 + lang-specific)
        # - "p/security-audit": Security-focused rules
        # - "p/owasp-top-ten": OWASP Top 10 vulnerabilities
        # - "p/python": Python-specific rules
        # - Custom path: Path to custom rules file/directory
        self.config = config.get("config", "auto")

        self.max_findings = config.get("max_findings", 50)
        self.severity_threshold = config.get("severity_threshold", "INFO")
        self.enricher = enricher  # Graph enrichment for cross-detector collaboration

        # Default exclude patterns
        default_exclude = [
            "tests/",
            "test_*.py",
            "*_test.py",
            "migrations/",
            ".venv/",
            "venv/",
            "node_modules/",
            "__pycache__/",
        ]
        self.exclude = config.get("exclude", default_exclude)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run Semgrep and enrich findings with graph data.

        Returns:
            List of security findings
        """
        logger.info(f"Running Semgrep on {self.repository_path}")

        # Run Semgrep and get results
        semgrep_findings = self._run_semgrep()

        if not semgrep_findings:
            logger.info("No security issues found by Semgrep")
            return []

        # Group by file
        findings_by_file: Dict[str, List[Dict]] = {}
        for sf in semgrep_findings[:self.max_findings]:
            file_path = sf["path"]
            if file_path not in findings_by_file:
                findings_by_file[file_path] = []
            findings_by_file[file_path].append(sf)

        # Create enriched findings
        findings = []
        for file_path, file_findings in findings_by_file.items():
            graph_context = self._get_file_context(file_path)

            for sf in file_findings:
                finding = self._create_finding(sf, graph_context)
                if finding:
                    findings.append(finding)

        logger.info(f"Created {len(findings)} security findings")
        return findings

    def _run_semgrep(self) -> List[Dict[str, Any]]:
        """Run Semgrep and parse JSON output.

        Returns:
            List of security finding dictionaries
        """
        try:
            # Build Semgrep command
            cmd = [
                "semgrep",
                "scan",
                "--json",
                "--quiet",  # Suppress progress bars
                f"--config={self.config}",
                "--jobs=4",  # Limit parallel jobs to avoid freezing
                "--max-memory=2000",  # Limit memory usage to 2GB
            ]

            # Add exclude patterns
            for pattern in self.exclude:
                cmd.extend(["--exclude", pattern])

            # Target path
            cmd.append(str(self.repository_path))

            # Run Semgrep
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.repository_path,
                    timeout=180  # Pattern matching, allow 3 minutes
                )
            except subprocess.TimeoutExpired:
                logger.warning(f"Semgrep timed out after 180s on {self.repository_path}")
                return []

            # Parse JSON output
            if not result.stdout:
                logger.info("Semgrep produced no output")
                return []

            output = json.loads(result.stdout)
            results = output.get("results", [])

            # Filter by severity threshold
            filtered_results = [
                r for r in results
                if self._meets_severity_threshold(r.get("extra", {}).get("severity", "INFO"))
            ]

            logger.info(f"Semgrep found {len(filtered_results)} security issues")
            return filtered_results

        except FileNotFoundError:
            logger.error("Semgrep not found. Install with: pip install semgrep")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Semgrep JSON output: {e}")
            return []
        except Exception as e:
            logger.error(f"Error running Semgrep: {e}")
            return []

    def _meets_severity_threshold(self, severity: str) -> bool:
        """Check if severity meets threshold.

        Args:
            severity: Semgrep severity level

        Returns:
            True if meets threshold
        """
        severity_order = {"INFO": 0, "WARNING": 1, "ERROR": 2}
        threshold_level = severity_order.get(self.severity_threshold, 0)
        finding_level = severity_order.get(severity, 0)
        return finding_level >= threshold_level

    def _create_finding(
        self,
        semgrep_finding: Dict[str, Any],
        graph_context: Dict[str, Any]
    ) -> Optional[Finding]:
        """Create finding from Semgrep result.

        Args:
            semgrep_finding: Semgrep finding dictionary
            graph_context: Graph context for file

        Returns:
            Finding object or None if creation fails
        """
        # Extract Semgrep data
        path = semgrep_finding.get("path", "")
        check_id = semgrep_finding.get("check_id", "")
        message = semgrep_finding.get("extra", {}).get("message", "")
        severity_str = semgrep_finding.get("extra", {}).get("severity", "INFO")
        metadata = semgrep_finding.get("extra", {}).get("metadata", {})

        # Location info
        start_line = semgrep_finding.get("start", {}).get("line", 0)
        end_line = semgrep_finding.get("end", {}).get("line", 0)

        # Map Semgrep severity to Repotoire severity
        severity = self.SEVERITY_MAP.get(severity_str, Severity.LOW)

        # Build description
        description = f"{message}\n\n"

        # Add metadata if available
        if metadata.get("cwe"):
            cwe_list = metadata["cwe"]
            if isinstance(cwe_list, list):
                description += f"**CWE**: {', '.join(cwe_list)}\n"

        if metadata.get("owasp"):
            owasp_list = metadata["owasp"]
            if isinstance(owasp_list, list):
                description += f"**OWASP**: {', '.join(owasp_list)}\n"

        if metadata.get("category"):
            description += f"**Category**: {metadata['category']}\n"

        if graph_context.get("file_loc"):
            description += f"**File Size**: {graph_context['file_loc']} LOC\n"

        description += f"\n**Impact**: Security vulnerability detected by Semgrep pattern matching.\n"

        # Create finding
        finding_id = str(uuid.uuid4())

        # Extract rule name from check_id (e.g., "python.lang.security.xxe" -> "xxe")
        rule_name = check_id.split(".")[-1] if "." in check_id else check_id

        finding = Finding(
            id=finding_id,
            detector="SemgrepDetector",
            severity=severity,
            title=f"Security issue: {rule_name}",
            description=description,
            affected_nodes=[],  # Semgrep doesn't know about graph nodes
            affected_files=[path],
            graph_context={
                "tool": "semgrep",
                "check_id": check_id,
                "start_line": start_line,
                "end_line": end_line,
                "severity": severity_str,
                "cwe": metadata.get("cwe", []),
                "owasp": metadata.get("owasp", []),
                "file_loc": graph_context.get("file_loc", 0),
            },
            suggested_fix=self._suggest_fix(metadata, message),
            estimated_effort=self._estimate_effort(severity_str, metadata),
            created_at=datetime.now()
        )

        # Flag entities in graph for cross-detector collaboration (REPO-151 Phase 2)
        # Note: Semgrep doesn't provide qualified names, so we flag by file:line
        if self.enricher:
            try:
                # Create a pseudo-qualified name for the security issue
                entity_qname = f"{path}:{start_line}"

                # Map severity to confidence (ERROR=high, WARNING=medium, INFO=low)
                confidence_map = {"ERROR": 0.95, "WARNING": 0.85, "INFO": 0.75}
                confidence_score = confidence_map.get(severity_str, 0.80)

                self.enricher.flag_entity(
                    entity_qualified_name=entity_qname,
                    detector="SemgrepDetector",
                    severity=severity.value,
                    issues=[check_id],
                    confidence=confidence_score,
                    metadata={
                        "check_id": check_id,
                        "rule_name": rule_name,
                        "file": path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "cwe": metadata.get("cwe", []),
                        "owasp": metadata.get("owasp", [])
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to flag entity at {path}:{start_line} in graph: {e}")

        # Add collaboration metadata to finding (REPO-150 Phase 1)
        confidence_map = {"ERROR": 0.95, "WARNING": 0.85, "INFO": 0.75}
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="SemgrepDetector",
                confidence=confidence_map.get(severity_str, 0.80),
                evidence=["semgrep", check_id, "external_tool"],
                tags=["semgrep", "security", self._get_category_tag(check_id, metadata)]
            )
        )

        return finding

    def _get_file_context(self, file_path: str) -> Dict[str, Any]:
        """Get context from Neo4j graph for file.

        Args:
            file_path: Relative file path

        Returns:
            Dictionary with graph context
        """
        # Normalize path for Neo4j
        normalized_path = file_path.replace("\\", "/")

        query = """
        MATCH (file:File {filePath: $file_path})
        RETURN file.loc as file_loc
        LIMIT 1
        """

        try:
            results = self.db.execute_query(query, {"file_path": normalized_path})
            if results:
                result = results[0]
                return {
                    "file_loc": result.get("file_loc", 0),
                }
        except Exception as e:
            logger.warning(f"Failed to enrich from graph: {e}")

        return {"file_loc": 0}

    def _suggest_fix(self, metadata: Dict[str, Any], message: str) -> str:
        """Suggest fix based on vulnerability type.

        Args:
            metadata: Semgrep finding metadata
            message: Semgrep message

        Returns:
            Fix suggestion
        """
        # Try to extract fix suggestion from metadata
        if metadata.get("fix"):
            return f"Recommended fix: {metadata['fix']}"

        # Generic fix suggestions based on category
        category = metadata.get("category", "").lower()

        if "injection" in category or "sql" in category:
            return "Use parameterized queries or ORM methods to prevent injection attacks"
        elif "xss" in category or "cross-site" in message.lower():
            return "Sanitize user input and escape output properly"
        elif "auth" in category or "authentication" in message.lower():
            return "Review authentication logic and ensure proper access controls"
        elif "crypto" in category or "encryption" in message.lower():
            return "Use cryptographically secure algorithms and proper key management"
        elif "path" in category or "traversal" in message.lower():
            return "Validate and sanitize file paths, use allowlist approach"
        else:
            return "Review the code and apply security best practices as per Semgrep recommendation"

    def _estimate_effort(self, severity: str, metadata: Dict[str, Any]) -> str:
        """Estimate effort to fix vulnerability.

        Args:
            severity: Semgrep severity level
            metadata: Finding metadata

        Returns:
            Effort estimate
        """
        if severity == "ERROR":
            return "High (half day to full day)"
        elif severity == "WARNING":
            return "Medium (2-4 hours)"
        else:
            return "Small (1-2 hours)"

    def _get_category_tag(self, check_id: str, metadata: Dict[str, Any]) -> str:
        """Get semantic category tag from Semgrep check ID and metadata.

        Args:
            check_id: Semgrep rule ID (e.g., "python.lang.security.sql-injection")
            metadata: Finding metadata with CWE/OWASP info

        Returns:
            Semantic category tag
        """
        # Map Semgrep patterns to semantic categories for cross-detector correlation
        check_id_lower = check_id.lower()

        # Check metadata first for precise categorization
        if metadata.get("category"):
            category = metadata["category"].lower()
            if "injection" in category or "sql" in category:
                return "injection"
            elif "xss" in category or "cross-site" in category:
                return "xss"
            elif "auth" in category:
                return "authentication"
            elif "crypto" in category:
                return "cryptography"

        # Fallback to check_id pattern matching
        if "injection" in check_id_lower or "sql" in check_id_lower:
            return "injection"
        elif "xss" in check_id_lower or "cross-site" in check_id_lower:
            return "xss"
        elif "auth" in check_id_lower or "authentication" in check_id_lower:
            return "authentication"
        elif "crypto" in check_id_lower or "encryption" in check_id_lower:
            return "cryptography"
        elif "path" in check_id_lower or "traversal" in check_id_lower:
            return "path_traversal"
        elif "command" in check_id_lower or "exec" in check_id_lower:
            return "command_injection"
        elif "xxe" in check_id_lower:
            return "xxe"
        elif "ssrf" in check_id_lower:
            return "ssrf"
        else:
            return "security_general"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a security finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
