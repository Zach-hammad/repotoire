"""jscpd-based duplicate code detector with Neo4j graph enrichment.

This hybrid detector combines jscpd's fast duplicate detection with Neo4j graph data
to provide detailed code duplication analysis with rich context.

Architecture:
    1. Run jscpd on repository (fast token-based duplicate detection)
    2. Parse jscpd JSON output
    3. Enrich findings with Neo4j graph data (file LOC, entity context)
    4. Generate detailed duplication findings

This approach achieves:
    - Fast duplicate detection (Rabin-Karp algorithm, O(n))
    - Rich context (graph-based metadata)
    - Actionable refactoring suggestions

Performance: ~5-30 seconds even on large codebases (vs 6-12+ minutes for Pylint R0801)
"""

import json
import subprocess
import tempfile
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


class JscpdDetector(CodeSmellDetector):
    """Detects duplicate code using jscpd with graph enrichment.

    Uses jscpd for fast duplicate detection and Neo4j for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        min_lines: Minimum lines for duplicate detection (default: 5)
        min_tokens: Minimum tokens for duplicate detection (default: 50)
        max_findings: Maximum findings to report (default: 50)
        threshold: Duplication percentage threshold (default: 10%)
    """

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict] = None, enricher: Optional[GraphEnricher] = None):
        """Initialize jscpd detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - min_lines: Min lines for duplicate
                - min_tokens: Min tokens for duplicate
                - max_findings: Max findings to report
                - threshold: Duplication percentage threshold
                - ignore: List of patterns to ignore (default: node_modules, .venv, __pycache__, etc.)
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.min_lines = config.get("min_lines", 5)
        self.min_tokens = config.get("min_tokens", 50)
        self.max_findings = config.get("max_findings", 50)
        self.threshold = config.get("threshold", 10.0)  # percentage
        self.enricher = enricher  # Graph enrichment for cross-detector collaboration

        # Default ignore patterns
        default_ignore = [
            "node_modules/**",
            ".venv/**",
            "venv/**",
            "env/**",
            "__pycache__/**",
            "*.pyc",
            ".git/**",
            "dist/**",
            "build/**",
            ".pytest_cache/**",
            "htmlcov/**",
            ".tox/**",
            "*.egg-info/**",
        ]
        self.ignore = config.get("ignore", default_ignore)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run jscpd and enrich findings with graph data.

        Returns:
            List of code duplication findings
        """
        logger.info(f"Running jscpd on {self.repository_path}")

        # Run jscpd and get results
        jscpd_results = self._run_jscpd()

        if not jscpd_results:
            logger.info("No code duplications found by jscpd")
            return []

        # Group duplicates by file for reporting
        findings = []
        for duplicate in jscpd_results[:self.max_findings]:
            finding = self._create_finding(duplicate)
            if finding:
                findings.append(finding)

        logger.info(f"Created {len(findings)} duplicate code findings")
        return findings

    def _run_jscpd(self) -> List[Dict[str, Any]]:
        """Run jscpd and parse JSON output.

        Returns:
            List of duplicate code dictionaries
        """
        try:
            # Create temporary output directory
            with tempfile.TemporaryDirectory() as temp_dir:
                # Build jscpd command
                cmd = [
                    "npx", "jscpd",
                    "--reporters", "json",
                    "--output", temp_dir,
                    "--format", "python",
                    "--min-lines", str(self.min_lines),
                    "--min-tokens", str(self.min_tokens),
                    "--threshold", str(self.threshold),
                ]

                # Add ignore patterns
                for pattern in self.ignore:
                    cmd.extend(["--ignore", pattern])

                # Only scan source code directory (repotoire/), not tests or other dirs
                # This avoids scanning test fixtures which have intentional duplication
                source_dir = self.repository_path / "repotoire"
                if source_dir.exists():
                    cmd.append(str(source_dir))
                else:
                    # Fallback to repository root if repotoire/ doesn't exist
                    cmd.append(str(self.repository_path))

                # Run jscpd (suppress terminal output)
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        cwd=self.repository_path,
                        timeout=120  # Duplicate detection, allow 2 minutes
                    )
                except subprocess.TimeoutExpired:
                    logger.warning(f"jscpd timed out after 120s on {self.repository_path}")
                    return []

                # Read JSON output
                report_path = Path(temp_dir) / "jscpd-report.json"
                if not report_path.exists():
                    logger.warning("jscpd did not generate report")
                    return []

                with open(report_path, "r") as f:
                    report = json.load(f)

                duplicates = report.get("duplicates", [])
                logger.info(f"jscpd found {len(duplicates)} duplicate code blocks")

                return duplicates

        except FileNotFoundError:
            logger.error("jscpd not found. Install with: npm install jscpd")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse jscpd JSON output: {e}")
            return []
        except Exception as e:
            logger.error(f"Error running jscpd: {e}")
            return []

    def _create_finding(self, duplicate: Dict[str, Any]) -> Optional[Finding]:
        """Create finding from jscpd duplicate result.

        Args:
            duplicate: jscpd duplicate dictionary

        Returns:
            Finding object or None if enrichment fails
        """
        # Extract jscpd data
        lines = duplicate.get("lines", 0)
        first_file = duplicate.get("firstFile", {})
        second_file = duplicate.get("secondFile", {})

        file1_name = first_file.get("name", "")
        file1_start = first_file.get("startLoc", {}).get("line", 0)
        file1_end = first_file.get("endLoc", {}).get("line", 0)

        file2_name = second_file.get("name", "")
        file2_start = second_file.get("startLoc", {}).get("line", 0)
        file2_end = second_file.get("endLoc", {}).get("line", 0)

        # Handle paths - jscpd returns relative paths
        file1_path = str(Path(file1_name))
        file2_path = str(Path(file2_name))

        # Enrich with graph data
        graph_data1 = self._get_file_graph_context(file1_path)
        graph_data2 = self._get_file_graph_context(file2_path)

        # Determine severity based on duplication size
        if lines >= 50:
            severity = Severity.HIGH
        elif lines >= 20:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="JscpdDetector",
            severity=severity,
            title=f"Duplicate code: {lines} lines duplicated",
            description=self._build_description(duplicate, graph_data1, graph_data2),
            affected_nodes=[],  # jscpd doesn't know about specific functions
            affected_files=[file1_path, file2_path],
            graph_context={
                "lines": lines,
                "file1": file1_path,
                "file1_start": file1_start,
                "file1_end": file1_end,
                "file2": file2_path,
                "file2_start": file2_start,
                "file2_end": file2_end,
                "file1_loc": graph_data1.get("file_loc", 0),
                "file2_loc": graph_data2.get("file_loc", 0),
            },
            suggested_fix=self._suggest_fix(lines, file1_path, file2_path),
            estimated_effort=self._estimate_effort(lines),
            created_at=datetime.now()
        )

        # Flag entities in graph for cross-detector collaboration (REPO-151 Phase 2)
        # Note: jscpd doesn't provide qualified names, flag both file locations
        if self.enricher:
            # Calculate confidence based on duplicate size (larger = more confident)
            if lines >= 50:
                confidence_score = 0.95
            elif lines >= 20:
                confidence_score = 0.90
            else:
                confidence_score = 0.85

            # Flag first location
            try:
                entity_qname1 = f"{file1_path}:{file1_start}"
                self.enricher.flag_entity(
                    entity_qualified_name=entity_qname1,
                    detector="JscpdDetector",
                    severity=severity.value,
                    issues=["duplicate_code"],
                    confidence=confidence_score,
                    metadata={
                        "lines": lines,
                        "file": file1_path,
                        "start_line": file1_start,
                        "end_line": file1_end,
                        "duplicate_of": file2_path
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to flag entity at {file1_path}:{file1_start} in graph: {e}")

            # Flag second location
            try:
                entity_qname2 = f"{file2_path}:{file2_start}"
                self.enricher.flag_entity(
                    entity_qualified_name=entity_qname2,
                    detector="JscpdDetector",
                    severity=severity.value,
                    issues=["duplicate_code"],
                    confidence=confidence_score,
                    metadata={
                        "lines": lines,
                        "file": file2_path,
                        "start_line": file2_start,
                        "end_line": file2_end,
                        "duplicate_of": file1_path
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to flag entity at {file2_path}:{file2_start} in graph: {e}")

        # Add collaboration metadata to finding (REPO-150 Phase 1)
        if lines >= 50:
            confidence_score = 0.95
        elif lines >= 20:
            confidence_score = 0.90
        else:
            confidence_score = 0.85

        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="JscpdDetector",
                confidence=confidence_score,
                evidence=["jscpd", f"duplicate_{lines}_lines", "external_tool"],
                tags=["jscpd", "duplication", "code_quality"]
            )
        )

        return finding

    def _get_file_graph_context(self, file_path: str) -> Dict[str, Any]:
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
        OPTIONAL MATCH (file)-[:CONTAINS]->(entity)
        RETURN
            file.loc as file_loc,
            count(entity) as entity_count
        """

        try:
            results = self.db.execute_query(query, {"file_path": normalized_path})
            if results:
                result = results[0]
                return {
                    "file_loc": result.get("file_loc", 0),
                    "entity_count": result.get("entity_count", 0)
                }
        except Exception as e:
            logger.warning(f"Failed to enrich from graph: {e}")

        return {"file_loc": 0, "entity_count": 0}

    def _build_description(
        self,
        duplicate: Dict[str, Any],
        graph_data1: Dict[str, Any],
        graph_data2: Dict[str, Any]
    ) -> str:
        """Build detailed description with context.

        Args:
            duplicate: jscpd duplicate data
            graph_data1: Graph data for first file
            graph_data2: Graph data for second file

        Returns:
            Formatted description
        """
        lines = duplicate.get("lines", 0)
        first_file = duplicate.get("firstFile", {})
        second_file = duplicate.get("secondFile", {})

        file1_name = first_file.get("name", "")
        file1_start = first_file.get("startLoc", {}).get("line", 0)
        file1_end = first_file.get("endLoc", {}).get("line", 0)

        file2_name = second_file.get("name", "")
        file2_start = second_file.get("startLoc", {}).get("line", 0)
        file2_end = second_file.get("endLoc", {}).get("line", 0)

        desc = f"Found {lines} lines of duplicated code.\n\n"
        desc += f"**Location 1**: {file1_name}:{file1_start}-{file1_end}\n"
        if graph_data1.get("file_loc"):
            desc += f"  - File Size: {graph_data1['file_loc']} LOC\n"

        desc += f"\n**Location 2**: {file2_name}:{file2_start}-{file2_end}\n"
        if graph_data2.get("file_loc"):
            desc += f"  - File Size: {graph_data2['file_loc']} LOC\n"

        desc += f"\n**Impact**: Code duplication increases maintenance burden and bug risk.\n"

        return desc

    def _suggest_fix(self, lines: int, file1: str, file2: str) -> str:
        """Suggest fix based on duplication size.

        Args:
            lines: Number of duplicated lines
            file1: First file path
            file2: Second file path

        Returns:
            Fix suggestion
        """
        if lines >= 50:
            return "Extract large duplicated block into a shared utility function or class"
        elif lines >= 20:
            return "Refactor duplicated code into a shared helper function"
        else:
            return "Consider extracting common logic to reduce duplication"

    def _estimate_effort(self, lines: int) -> str:
        """Estimate effort to fix duplication.

        Args:
            lines: Number of duplicated lines

        Returns:
            Effort estimate
        """
        if lines >= 50:
            return "Medium (half day)"
        elif lines >= 20:
            return "Small (1-2 hours)"
        else:
            return "Small (30 minutes - 1 hour)"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a duplication finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
