"""Self-Admitted Technical Debt (SATD) detector with Neo4j graph enrichment.

This hybrid detector scans code comments for SATD patterns (TODO, FIXME, HACK, etc.)
and enriches findings with graph context (containing function/class).

Research shows SATD comments capture 20-30% of technical debt that other detectors miss.

Architecture:
    1. Use Rust scanner (repotoire_fast.scan_satd_batch) for parallel processing
    2. Fall back to Python regex if Rust unavailable
    3. Enrich findings with Neo4j graph data (containing entity)
    4. Generate detailed findings with context

Performance:
    - Rust path: ~50-100x faster than Python regex
    - Python fallback: ~1-2 seconds for 100 files

SATD Patterns and Severity:
    - HIGH: HACK, KLUDGE, BUG (known bugs or workarounds)
    - MEDIUM: FIXME, XXX, REFACTOR (issues needing attention)
    - LOW: TODO, TEMP (reminders for future work)
"""

import re
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

# Try to import Rust fast scanner
try:
    import repotoire_fast

    HAS_RUST = True
except ImportError:
    HAS_RUST = False
    logger.debug("repotoire_fast not available, using Python fallback for SATD scanning")


# SATD pattern matching (Python fallback)
SATD_PATTERN = re.compile(
    r"(?i)(?:#|//|/\*|\*|\"\"\"|\'\'\')?\s*\b(TODO|FIXME|HACK|XXX|KLUDGE|REFACTOR|TEMP|BUG)\b[\s:(\[]*(.{0,200})",
    re.IGNORECASE,
)

# Severity mapping for SATD types
SATD_SEVERITY_MAP: Dict[str, Severity] = {
    # High severity: known bugs or workarounds
    "HACK": Severity.HIGH,
    "KLUDGE": Severity.HIGH,
    "BUG": Severity.HIGH,
    # Medium severity: issues needing attention
    "FIXME": Severity.MEDIUM,
    "XXX": Severity.MEDIUM,
    "REFACTOR": Severity.MEDIUM,
    # Low severity: reminders for future work
    "TODO": Severity.LOW,
    "TEMP": Severity.LOW,
}


class SATDDetector(CodeSmellDetector):
    """Detects Self-Admitted Technical Debt comments with graph enrichment.

    Scans code comments for TODO, FIXME, HACK, XXX, KLUDGE, REFACTOR, TEMP,
    and BUG patterns. Uses Rust for fast parallel scanning with Python fallback.

    Configuration:
        repository_path: Path to repository root (required)
        max_findings: Maximum findings to report (default: 500)
        exclude_patterns: File patterns to exclude (default: tests, migrations)
        file_extensions: Extensions to scan (default: .py, .js, .ts, .java, .go, .rs)
    """

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize SATD detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - max_findings: Maximum findings to report
                - exclude_patterns: File patterns to exclude
                - file_extensions: Extensions to scan
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 500)
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

        # File extensions to scan
        default_extensions = [".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".c", ".cpp", ".h"]
        self.file_extensions = config.get("file_extensions", default_extensions)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Scan for SATD comments and enrich with graph data.

        Returns:
            List of SATD findings
        """
        logger.info(f"Scanning for SATD comments in {self.repository_path}")

        # Collect files to scan
        files_to_scan = self._collect_files()
        if not files_to_scan:
            logger.info("No files to scan for SATD")
            return []

        logger.debug(f"Scanning {len(files_to_scan)} files for SATD patterns")

        # Scan files (Rust or Python fallback)
        if HAS_RUST:
            raw_findings = self._scan_rust(files_to_scan)
        else:
            raw_findings = self._scan_python(files_to_scan)

        if not raw_findings:
            logger.info("No SATD comments found")
            return []

        # Group by file for efficient graph enrichment
        findings_by_file: Dict[str, List[Tuple]] = {}
        for finding in raw_findings[: self.max_findings]:
            file_path = finding[0]
            if file_path not in findings_by_file:
                findings_by_file[file_path] = []
            findings_by_file[file_path].append(finding)

        # Create enriched findings
        findings = []
        for file_path, file_findings in findings_by_file.items():
            for raw in file_findings:
                finding = self._create_finding(raw)
                if finding:
                    findings.append(finding)

        # Log summary by type
        type_counts: Dict[str, int] = {}
        for f in findings:
            satd_type = f.graph_context.get("satd_type", "UNKNOWN")
            type_counts[satd_type] = type_counts.get(satd_type, 0) + 1

        logger.info(f"Found {len(findings)} SATD comments: {type_counts}")
        return findings

    def _collect_files(self) -> List[Tuple[str, str]]:
        """Collect files to scan with their content.

        Returns:
            List of (relative_path, content) tuples
        """
        files = []

        for ext in self.file_extensions:
            for path in self.repository_path.rglob(f"*{ext}"):
                # Skip excluded patterns
                rel_path = str(path.relative_to(self.repository_path))
                if self._should_exclude(rel_path):
                    continue

                # Read file content
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    # Skip very large files (likely generated or minified)
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

    def _scan_rust(self, files: List[Tuple[str, str]]) -> List[Tuple]:
        """Scan files using Rust parallel scanner.

        Args:
            files: List of (path, content) tuples

        Returns:
            List of (file_path, line_number, satd_type, comment_text, severity) tuples
        """
        try:
            return repotoire_fast.scan_satd_batch(files)
        except Exception as e:
            logger.warning(f"Rust SATD scanner failed, falling back to Python: {e}")
            return self._scan_python(files)

    def _scan_python(self, files: List[Tuple[str, str]]) -> List[Tuple]:
        """Scan files using Python regex (fallback).

        Args:
            files: List of (path, content) tuples

        Returns:
            List of (file_path, line_number, satd_type, comment_text, severity) tuples
        """
        findings = []

        for file_path, content in files:
            for line_idx, line in enumerate(content.splitlines()):
                line_number = line_idx + 1

                # Skip very long lines
                if len(line) > 2000:
                    continue

                for match in SATD_PATTERN.finditer(line):
                    satd_type = match.group(1).upper()
                    comment_text = match.group(2).strip() if match.group(2) else ""

                    # Clean up comment text
                    comment_text = comment_text.rstrip("*/# ").strip()

                    # Get severity
                    severity = SATD_SEVERITY_MAP.get(satd_type, Severity.LOW)

                    findings.append(
                        (file_path, line_number, satd_type, comment_text, severity.value)
                    )

        return findings

    def _create_finding(self, raw: Tuple) -> Optional[Finding]:
        """Create Finding from raw SATD data with graph enrichment.

        Args:
            raw: Tuple of (file_path, line_number, satd_type, comment_text, severity)

        Returns:
            Finding object or None if creation fails
        """
        file_path, line_number, satd_type, comment_text, severity_str = raw

        # Get graph context (containing function/class)
        graph_context = self._get_graph_context(file_path, line_number)

        # Map severity string to enum
        severity = Severity(severity_str) if isinstance(severity_str, str) else severity_str

        # Build title and description
        title = f"SATD: {satd_type}"
        if comment_text:
            # Truncate long comments
            short_comment = comment_text[:80] + "..." if len(comment_text) > 80 else comment_text
            title = f"SATD: {satd_type} - {short_comment}"

        description = self._build_description(
            satd_type, comment_text, file_path, line_number, graph_context
        )

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="SATDDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=graph_context.get("nodes", []),
            affected_files=[file_path],
            line_start=line_number,
            line_end=line_number,
            graph_context={
                "satd_type": satd_type,
                "comment_text": comment_text,
                "containing_entity": graph_context.get("containing_entity"),
                "entity_type": graph_context.get("entity_type"),
                **graph_context,
            },
            suggested_fix=self._suggest_fix(satd_type, comment_text),
            estimated_effort=self._estimate_effort(satd_type),
            created_at=datetime.now(),
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_context.get("nodes"):
            for node in graph_context["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="SATDDetector",
                        severity=severity.value,
                        issues=[satd_type],
                        confidence=self._confidence_score(satd_type),
                        metadata={
                            "satd_type": satd_type,
                            "comment_text": comment_text[:200],
                            "file": file_path,
                            "line": line_number,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="SATDDetector",
                confidence=self._confidence_score(satd_type),
                evidence=[satd_type.lower(), "comment_analysis"],
                tags=["satd", satd_type.lower(), "technical_debt"],
            )
        )

        return finding

    def _get_graph_context(self, file_path: str, line: int) -> Dict:
        """Get context from Neo4j graph.

        Finds the containing function or class for the SATD comment.

        Args:
            file_path: Relative file path
            line: Line number

        Returns:
            Dictionary with graph context
        """
        # Normalize path for Neo4j
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
            results = self.db.execute_query(
                query, {"file_path": normalized_path, "line": line}
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

    def _build_description(
        self,
        satd_type: str,
        comment_text: str,
        file_path: str,
        line_number: int,
        graph_context: Dict,
    ) -> str:
        """Build detailed description with context.

        Args:
            satd_type: Type of SATD (TODO, FIXME, etc.)
            comment_text: The comment text
            file_path: File path
            line_number: Line number
            graph_context: Graph enrichment data

        Returns:
            Formatted description
        """
        desc = f"**Self-Admitted Technical Debt ({satd_type})**\n\n"

        if comment_text:
            desc += f"**Comment**: {comment_text}\n\n"

        desc += f"**Location**: {file_path}:{line_number}\n"

        if graph_context.get("containing_entity"):
            entity_type = graph_context.get("entity_type", "entity")
            desc += f"**Containing {entity_type}**: `{graph_context['containing_entity']}`\n"

        if graph_context.get("complexity"):
            desc += f"**Entity Complexity**: {graph_context['complexity']}\n"

        # Add context about severity
        desc += f"\n**Severity Rationale**:\n"
        if satd_type in ("HACK", "KLUDGE", "BUG"):
            desc += "- HIGH: Indicates a known bug, workaround, or hack that needs immediate attention\n"
        elif satd_type in ("FIXME", "XXX", "REFACTOR"):
            desc += "- MEDIUM: Indicates an issue that should be addressed soon\n"
        else:
            desc += "- LOW: Reminder for future work\n"

        return desc

    def _suggest_fix(self, satd_type: str, comment_text: str) -> str:
        """Suggest fix based on SATD type.

        Args:
            satd_type: Type of SATD
            comment_text: The comment text

        Returns:
            Fix suggestion
        """
        suggestions = {
            "TODO": "Review and either implement the TODO or create a tracking issue",
            "FIXME": "Investigate and fix the issue described in the comment",
            "HACK": "Replace the hacky workaround with a proper solution",
            "XXX": "Review and address the concern mentioned in the comment",
            "KLUDGE": "Refactor this code to remove the kludge/workaround",
            "REFACTOR": "Schedule time to refactor as described",
            "TEMP": "Remove the temporary code before release",
            "BUG": "Fix the known bug and add a regression test",
        }

        base_suggestion = suggestions.get(satd_type, "Review and address this technical debt")

        if comment_text:
            return f"{base_suggestion}. Comment indicates: '{comment_text[:100]}'"
        return base_suggestion

    def _estimate_effort(self, satd_type: str) -> str:
        """Estimate effort to fix.

        Args:
            satd_type: Type of SATD

        Returns:
            Effort estimate
        """
        # High severity items typically need more effort
        if satd_type in ("HACK", "KLUDGE", "BUG"):
            return "Medium (1-4 hours)"
        elif satd_type in ("REFACTOR",):
            return "Large (4+ hours)"
        else:
            return "Small (30-60 minutes)"

    def _confidence_score(self, satd_type: str) -> float:
        """Get confidence score for SATD type.

        Args:
            satd_type: Type of SATD

        Returns:
            Confidence score (0.0-1.0)
        """
        # SATD comments are explicit admissions, so high confidence
        confidence_map = {
            "BUG": 0.95,      # Developer explicitly marked as bug
            "HACK": 0.95,    # Developer explicitly marked as hack
            "KLUDGE": 0.95,  # Developer explicitly marked as kludge
            "FIXME": 0.90,   # Developer explicitly marked for fixing
            "XXX": 0.85,     # Common but sometimes informal
            "REFACTOR": 0.90,  # Explicit refactoring request
            "TODO": 0.80,    # Could be future work or immediate need
            "TEMP": 0.85,    # Explicit temporary code
        }
        return confidence_map.get(satd_type, 0.80)

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a SATD finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
