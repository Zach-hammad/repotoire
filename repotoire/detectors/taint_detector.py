"""Taint tracking detector for security vulnerability detection (REPO-411).

This detector uses data flow analysis to trace potentially malicious data from
untrusted sources (user input, network, files) to dangerous sinks (SQL queries,
command execution, eval). It detects:

- SQL injection (user input in database queries)
- Command injection (user input in shell commands)
- Code injection (user input in eval/exec)
- Path traversal (user input in file operations)
- XSS (user input in rendered templates)
- SSRF (user input in HTTP requests)
- Log injection (user input in log messages)

Architecture:
    1. Use Rust DFG extractor (repotoire_fast.extract_dataflow) for performance
    2. Use Rust taint analyzer (repotoire_fast.find_taint_flows)
    3. Enrich findings with Neo4j graph context (containing function/class)
    4. Generate detailed findings with vulnerability classification

Performance:
    - Rust DFG extraction: ~10-50x faster than Python AST walking
    - Parallel file processing via rayon
    - Sub-second analysis for codebases up to 10k LOC
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import Neo4jClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)

# Try to import Rust fast taint analyzer
try:
    import repotoire_fast

    HAS_RUST = True
except ImportError:
    HAS_RUST = False
    logger.debug("repotoire_fast not available, TaintDetector disabled")


# Vulnerability severity mapping
VULNERABILITY_SEVERITY: Dict[str, Severity] = {
    "sql_injection": Severity.CRITICAL,
    "command_injection": Severity.CRITICAL,
    "code_injection": Severity.CRITICAL,
    "template_injection": Severity.CRITICAL,
    "path_traversal": Severity.HIGH,
    "xss": Severity.HIGH,
    "ssrf": Severity.HIGH,
    "ldap_injection": Severity.HIGH,
    "data_leak": Severity.HIGH,
    "log_injection": Severity.MEDIUM,
}

# CWE ID mapping for vulnerability types
VULNERABILITY_CWE: Dict[str, str] = {
    "sql_injection": "CWE-89",
    "command_injection": "CWE-78",
    "code_injection": "CWE-94",
    "template_injection": "CWE-1336",
    "path_traversal": "CWE-22",
    "xss": "CWE-79",
    "ssrf": "CWE-918",
    "ldap_injection": "CWE-90",
    "data_leak": "CWE-200",
    "log_injection": "CWE-117",
}


class TaintDetector(CodeSmellDetector):
    """Detects security vulnerabilities through taint tracking.

    Uses data flow analysis to trace untrusted data from sources to sinks,
    identifying potential injection attacks and data leakage.

    Configuration:
        repository_path: Path to repository root (required)
        max_findings: Maximum findings to report (default: 100)
        exclude_patterns: File patterns to exclude (default: tests, migrations)
        include_sanitized: Include sanitized flows as LOW severity (default: False)
        custom_sources: Additional source patterns
        custom_sinks: Additional sink patterns
        custom_sanitizers: Additional sanitizer patterns
    """

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize taint detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - max_findings: Maximum findings to report
                - exclude_patterns: File patterns to exclude
                - include_sanitized: Report sanitized flows at LOW severity
                - custom_sources: Additional taint source patterns
                - custom_sinks: Additional taint sink patterns
                - custom_sanitizers: Additional sanitizer patterns
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)
        self.include_sanitized = config.get("include_sanitized", False)
        self.enricher = enricher

        # Default exclude patterns
        default_exclude = [
            "tests/",
            "test_*.py",
            "*_test.py",
            "migrations/",
            "__pycache__/",
            ".git/",
            "node_modules/",
            "venv/",
            ".venv/",
        ]
        self.exclude_patterns = config.get("exclude_patterns", default_exclude)

        # Custom patterns (extend defaults)
        self.custom_sources = config.get("custom_sources", [])
        self.custom_sinks = config.get("custom_sinks", [])
        self.custom_sanitizers = config.get("custom_sanitizers", [])

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Scan for taint flows and generate security findings.

        Returns:
            List of security findings for detected taint flows
        """
        if not HAS_RUST:
            logger.warning("TaintDetector requires repotoire_fast Rust module")
            return []

        logger.info(f"Scanning for taint flows in {self.repository_path}")

        # Collect Python files to scan
        files_to_scan = self._collect_files()
        if not files_to_scan:
            logger.info("No Python files to scan for taint flows")
            return []

        logger.debug(f"Scanning {len(files_to_scan)} files for taint flows")

        # Find taint flows using Rust analyzer
        try:
            raw_results = repotoire_fast.find_taint_flows_batch(files_to_scan)
        except Exception as e:
            logger.error(f"Rust taint analysis failed: {e}")
            return []

        # Flatten results
        all_flows = []
        for file_path, flows in raw_results:
            for flow in flows:
                all_flows.append((file_path, flow))

        if not all_flows:
            logger.info("No taint flows detected")
            return []

        # Filter flows
        filtered_flows = []
        for file_path, flow in all_flows:
            # Skip sanitized flows unless configured to include them
            if flow.has_sanitizer and not self.include_sanitized:
                continue
            filtered_flows.append((file_path, flow))

        # Create findings (limit to max_findings)
        findings = []
        for file_path, flow in filtered_flows[: self.max_findings]:
            finding = self._create_finding(file_path, flow)
            if finding:
                findings.append(finding)

        # Log summary
        vuln_counts: Dict[str, int] = {}
        for finding in findings:
            vuln = finding.graph_context.get("vulnerability", "unknown")
            vuln_counts[vuln] = vuln_counts.get(vuln, 0) + 1

        logger.info(f"Found {len(findings)} taint flows: {vuln_counts}")
        return findings

    def _collect_files(self) -> List[Tuple[str, str]]:
        """Collect Python files to scan with their content.

        Returns:
            List of (relative_path, content) tuples
        """
        files = []

        for path in self.repository_path.rglob("*.py"):
            # Skip excluded patterns
            rel_path = str(path.relative_to(self.repository_path))
            if self._should_exclude(rel_path):
                continue

            # Read file content
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                # Skip very large files
                if len(content) > 1_000_000:
                    continue
                files.append((rel_path, content))
            except (OSError, UnicodeDecodeError) as e:
                logger.debug(f"Skipping {rel_path}: {e}")
                continue

        return files

    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded.

        Args:
            path: Relative path to check

        Returns:
            True if path should be excluded
        """
        for pattern in self.exclude_patterns:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in path.split("/"):
                    return True
            elif "*" in pattern:
                import fnmatch

                if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
                    return True
            elif pattern in path:
                return True
        return False

    def _create_finding(self, file_path: str, flow) -> Optional[Finding]:
        """Create Finding from taint flow with graph enrichment.

        Args:
            file_path: Path to the file containing the flow
            flow: PyTaintFlow object from Rust

        Returns:
            Finding object or None if creation fails
        """
        # Get graph context
        graph_context = self._get_graph_context(file_path, flow.source_line, flow.sink_line)

        # Determine severity
        if flow.has_sanitizer:
            severity = Severity.LOW  # Sanitized flows are lower risk
        else:
            severity = VULNERABILITY_SEVERITY.get(flow.vulnerability, Severity.HIGH)

        # Build title
        vuln_title = flow.vulnerability.replace("_", " ").title()
        cwe = VULNERABILITY_CWE.get(flow.vulnerability, "")
        title = f"Potential {vuln_title}"
        if cwe:
            title += f" ({cwe})"

        # Build description
        description = self._build_description(file_path, flow, graph_context)

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="TaintDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=graph_context.get("nodes", []),
            affected_files=[file_path],
            line_start=flow.source_line,
            line_end=flow.sink_line,
            graph_context={
                "vulnerability": flow.vulnerability,
                "source": flow.source,
                "source_line": flow.source_line,
                "source_category": flow.source_category,
                "sink": flow.sink,
                "sink_line": flow.sink_line,
                "path": flow.path,
                "path_lines": flow.path_lines,
                "scope": flow.scope,
                "has_sanitizer": flow.has_sanitizer,
                "cwe": cwe,
                **graph_context,
            },
            suggested_fix=self._suggest_fix(flow.vulnerability, flow.has_sanitizer),
            estimated_effort=self._estimate_effort(flow.vulnerability),
            created_at=datetime.now(),
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_context.get("nodes"):
            for node in graph_context["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="TaintDetector",
                        severity=severity.value,
                        issues=[flow.vulnerability],
                        confidence=self._confidence_score(flow),
                        metadata={
                            "vulnerability": flow.vulnerability,
                            "cwe": cwe,
                            "source": flow.source,
                            "sink": flow.sink,
                            "file": file_path,
                            "has_sanitizer": flow.has_sanitizer,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="TaintDetector",
                confidence=self._confidence_score(flow),
                evidence=["taint_analysis", "data_flow", flow.source_category],
                tags=["security", flow.vulnerability, cwe.lower() if cwe else "vulnerability"],
            )
        )

        return finding

    def _get_graph_context(self, file_path: str, source_line: int, sink_line: int) -> Dict:
        """Get context from Neo4j graph.

        Finds the containing function or class for the taint flow.

        Args:
            file_path: Relative file path
            source_line: Source line number
            sink_line: Sink line number

        Returns:
            Dictionary with graph context
        """
        normalized_path = file_path.replace("\\", "/")

        query = """
        MATCH (file:File {filePath: $file_path})
        OPTIONAL MATCH (file)-[:CONTAINS]->(entity)
        WHERE entity.lineStart <= $line AND entity.lineEnd >= $line
        WITH file, entity
        ORDER BY entity.lineStart DESC
        LIMIT 1
        RETURN
            file.loc as file_loc,
            entity.qualifiedName as containing_entity,
            labels(entity)[0] as entity_type,
            entity.complexity as complexity
        """

        try:
            # Get context at sink location (typically more relevant)
            results = self.db.execute_query(
                query, {"file_path": normalized_path, "line": sink_line}
            )

            if results:
                result = results[0]
                containing_entity = result.get("containing_entity")
                return {
                    "file_loc": result.get("file_loc", 0),
                    "containing_entity": containing_entity,
                    "entity_type": result.get("entity_type"),
                    "complexity": result.get("complexity", 0),
                    "nodes": [containing_entity] if containing_entity else [],
                }
        except Exception as e:
            logger.debug(f"Failed to get graph context: {e}")

        return {"file_loc": 0, "containing_entity": None, "entity_type": None, "nodes": []}

    def _build_description(self, file_path: str, flow, graph_context: Dict) -> str:
        """Build detailed description with context.

        Args:
            file_path: File path
            flow: TaintFlow object
            graph_context: Graph enrichment data

        Returns:
            Formatted description
        """
        vuln_title = flow.vulnerability.replace("_", " ").title()
        cwe = VULNERABILITY_CWE.get(flow.vulnerability, "")

        desc = f"**Potential {vuln_title}**"
        if cwe:
            desc += f" ({cwe})"
        desc += "\n\n"

        desc += f"**Location**: {file_path}:{flow.source_line}-{flow.sink_line}\n\n"

        desc += f"**Taint Source**: `{flow.source}` (line {flow.source_line})\n"
        desc += f"  - Category: {flow.source_category}\n\n"

        desc += f"**Dangerous Sink**: `{flow.sink}` (line {flow.sink_line})\n\n"

        if len(flow.path) > 2:
            desc += "**Data Flow Path**:\n"
            for i, (var, line) in enumerate(zip(flow.path, flow.path_lines)):
                prefix = "  → " if i > 0 else "  "
                desc += f"{prefix}`{var}` (line {line})\n"
            desc += "\n"

        if flow.has_sanitizer:
            desc += "**Note**: A sanitizer function was detected in the data flow path. "
            desc += "This may neutralize the vulnerability, but verify the sanitization is appropriate.\n\n"

        if graph_context.get("containing_entity"):
            entity_type = graph_context.get("entity_type", "entity")
            desc += f"**Containing {entity_type}**: `{graph_context['containing_entity']}`\n"

        if graph_context.get("complexity"):
            desc += f"**Entity Complexity**: {graph_context['complexity']}\n"

        # Add vulnerability-specific information
        desc += "\n**Vulnerability Details**:\n"
        vuln_descriptions = {
            "sql_injection": "User input flows to a SQL query without proper parameterization. "
                           "An attacker could manipulate the query to access or modify data.",
            "command_injection": "User input flows to a shell command. "
                                "An attacker could execute arbitrary system commands.",
            "code_injection": "User input flows to dynamic code execution (eval/exec). "
                             "An attacker could execute arbitrary Python code.",
            "path_traversal": "User input flows to a file path. "
                             "An attacker could access files outside the intended directory.",
            "xss": "User input flows to rendered output. "
                  "An attacker could inject malicious scripts.",
            "ssrf": "User input flows to an HTTP request URL. "
                   "An attacker could make requests to internal services.",
            "log_injection": "User input flows to log output. "
                            "An attacker could forge log entries or inject malicious data.",
            "template_injection": "User input flows to template rendering. "
                                 "An attacker could execute server-side code.",
            "ldap_injection": "User input flows to an LDAP query. "
                             "An attacker could manipulate directory lookups.",
            "data_leak": "Sensitive data flows to an external destination. "
                        "Data could be exposed to unauthorized parties.",
        }
        desc += vuln_descriptions.get(flow.vulnerability, "Potential security vulnerability detected.")

        return desc

    def _suggest_fix(self, vulnerability: str, has_sanitizer: bool) -> str:
        """Suggest fix based on vulnerability type.

        Args:
            vulnerability: Type of vulnerability
            has_sanitizer: Whether a sanitizer was detected

        Returns:
            Fix suggestion
        """
        if has_sanitizer:
            return "A sanitizer is present but verify it's appropriate for this context. " \
                   "Ensure the sanitization method matches the sink type."

        suggestions = {
            "sql_injection": "Use parameterized queries or prepared statements. "
                            "Example: cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))",
            "command_injection": "Avoid shell commands with user input. "
                                "Use subprocess with shell=False and explicit argument list. "
                                "Example: subprocess.run(['ls', '-l', path], shell=False)",
            "code_injection": "Never use eval/exec with user input. "
                             "Use safer alternatives like ast.literal_eval for data parsing.",
            "path_traversal": "Use os.path.basename() to strip directory components, "
                             "or validate paths against an allowed list. "
                             "Example: safe_path = os.path.join(base_dir, os.path.basename(user_path))",
            "xss": "Escape HTML output using framework-provided functions. "
                  "Example: markupsafe.escape(user_input) or use autoescape templates.",
            "ssrf": "Validate URLs against an allowlist of permitted domains. "
                   "Block requests to internal IP ranges (127.0.0.1, 10.x.x.x, etc.).",
            "log_injection": "Sanitize user input before logging. "
                            "Remove newlines and special characters that could forge log entries.",
            "template_injection": "Use safe template rendering without string formatting. "
                                 "Pass variables to templates rather than building template strings.",
            "ldap_injection": "Use LDAP library escaping functions for user input. "
                             "Example: ldap.filter.escape_filter_chars(user_input)",
            "data_leak": "Review what data is being sent externally. "
                        "Implement data classification and restrict sensitive data transmission.",
        }

        return suggestions.get(vulnerability, "Review and sanitize user input before use.")

    def _estimate_effort(self, vulnerability: str) -> str:
        """Estimate effort to fix.

        Args:
            vulnerability: Type of vulnerability

        Returns:
            Effort estimate
        """
        # Critical injection issues often require significant refactoring
        high_effort = {"sql_injection", "command_injection", "code_injection", "template_injection"}
        medium_effort = {"path_traversal", "ssrf", "xss", "ldap_injection"}

        if vulnerability in high_effort:
            return "Medium-High (2-8 hours)"
        elif vulnerability in medium_effort:
            return "Medium (1-4 hours)"
        else:
            return "Low-Medium (30 min - 2 hours)"

    def _confidence_score(self, flow) -> float:
        """Calculate confidence score for taint flow.

        Args:
            flow: TaintFlow object

        Returns:
            Confidence score (0.0-1.0)
        """
        base_confidence = 0.85  # Taint analysis is generally reliable

        # Reduce confidence if sanitizer detected (may be false positive)
        if flow.has_sanitizer:
            base_confidence *= 0.5

        # Longer paths slightly reduce confidence (more opportunities for sanitization)
        path_penalty = min(0.15, len(flow.path) * 0.02)
        base_confidence -= path_penalty

        # Source category affects confidence
        category_modifiers = {
            "user_input": 0.0,      # Direct user input is high confidence
            "network": -0.05,       # Network data might be internal
            "file": -0.10,          # File data might be trusted
            "database": -0.10,      # DB data might be sanitized
            "environment": -0.15,   # Env vars might be controlled
            "external": -0.05,
        }
        base_confidence += category_modifiers.get(flow.source_category, 0)

        return max(0.3, min(0.95, base_confidence))

    def severity(self, finding: Finding) -> Severity:
        """Return severity for a finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        return finding.severity
