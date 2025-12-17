"""Ruff-based unused import detector.

Integrates ruff's accurate AST-based unused import detection (F401)
with graph context for enhanced reporting and analysis.

This hybrid approach combines:
- Ruff's accurate detection (no false positives from stdlib)
- Graph metadata (file LOC, complexity, relationships)
- Consistent reporting with other detectors
"""

import subprocess
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class RuffImportDetector(CodeSmellDetector):
    """Detects unused imports using ruff with graph-based enrichment."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None, enricher: Optional[GraphEnricher] = None):
        super().__init__(neo4j_client)
        config = detector_config or {}
        self.repository_path = config.get("repository_path", ".")
        self.enricher = enricher  # Graph enrichment for cross-detector collaboration
        self.logger = get_logger(__name__)

    def detect(self) -> List[Finding]:
        """
        Detect unused imports using ruff, enriched with graph context.

        Returns:
            List of Finding objects for unused imports.
        """
        # Run ruff to detect unused imports (F401)
        ruff_findings = self._run_ruff()

        if not ruff_findings:
            self.logger.info("No unused imports found by ruff")
            return []

        self.logger.info(f"Ruff found {len(ruff_findings)} unused imports")

        # Group by file
        findings_by_file: Dict[str, List[Dict[str, Any]]] = {}
        for ruff_finding in ruff_findings:
            file_path = ruff_finding["filename"]
            if file_path not in findings_by_file:
                findings_by_file[file_path] = []
            findings_by_file[file_path].append(ruff_finding)

        # Create findings with graph context
        findings = []
        for file_path, file_findings in findings_by_file.items():
            # Get graph context for file
            graph_context = self._get_file_context(file_path)

            # Extract import names
            imports = []
            for rf in file_findings:
                import_name = self._extract_import_name(rf["message"])
                imports.append({
                    "name": import_name,
                    "line": rf["location"]["row"],
                    "column": rf["location"]["column"],
                    "message": rf["message"],
                })

            # Determine severity based on count
            count = len(imports)
            if count >= 5:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Build description
            import_list = "\n".join([
                f"  • {imp['name']} (line {imp['line']})"
                for imp in imports
            ])

            description = (
                f"File '{file_path}' has {count} unused import(s):\n\n"
                f"{import_list}\n\n"
                f"These imports are detected by ruff's AST analysis and are safe to remove."
            )

            if graph_context:
                description += f"\n\nFile context: {graph_context['loc']} LOC"

            # Build suggested fix
            suggested_fix = "Run: ruff check --select F401 --fix " + file_path

            # Create finding
            finding = Finding(
                id=f"ruff_imports_{file_path.replace('/', '_').replace('.', '_')}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Unused imports in {Path(file_path).name}",
                description=description,
                affected_files=[file_path],
                affected_nodes=[],
                suggested_fix=suggested_fix,
                graph_context={
                    "tool": "ruff",
                    "rule": "F401",
                    "import_count": count,
                    "imports": imports,
                    "file_loc": graph_context.get("loc") if graph_context else None,
                    "file_complexity": graph_context.get("complexity") if graph_context else None,
                },
            )

            # Flag entities in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher:
                for imp in imports:
                    try:
                        # Create qualified name for unused import
                        entity_qname = f"{file_path}:{imp['name']}"

                        self.enricher.flag_entity(
                            entity_qualified_name=entity_qname,
                            detector="RuffImportDetector",
                            severity=severity.value,
                            issues=["F401"],
                            confidence=0.95,  # Ruff is highly accurate for unused imports
                            metadata={
                                "import_name": imp["name"],
                                "file": file_path,
                                "line": imp["line"],
                                "column": imp.get("column", 0)
                            }
                        )
                    except Exception as e:
                        self.logger.warning(f"Failed to flag import {imp['name']} in graph: {e}")

            # Add collaboration metadata to finding (REPO-150 Phase 1)
            finding.add_collaboration_metadata(
                CollaborationMetadata(
                    detector="RuffImportDetector",
                    confidence=0.95,
                    evidence=["ruff", "F401", "external_tool"],
                    tags=["ruff", "unused_import", "code_quality"]
                )
            )

            findings.append(finding)

        self.logger.info(f"RuffImportDetector created {len(findings)} findings")
        return findings

    def _run_ruff(self) -> List[Dict[str, Any]]:
        """
        Run ruff to detect unused imports.

        Returns:
            List of ruff finding dictionaries.
        """
        try:
            # Run ruff with JSON output
            try:
                result = subprocess.run(
                    [
                        "ruff", "check",
                        "--select", "F401",  # Unused imports only
                        "--output-format", "json",
                        str(self.repository_path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,  # Don't raise on non-zero exit (expected for findings)
                    timeout=60,  # Ruff is fast (Rust-based), 60s is generous
                )
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Ruff import check timed out after 60s on {self.repository_path}")
                return []

            # Parse JSON output
            if result.stdout:
                findings = json.loads(result.stdout)
                # Filter to only unused imports (F401)
                return [f for f in findings if f["code"] == "F401"]

            return []

        except FileNotFoundError:
            self.logger.error("ruff not found - install with: pip install ruff")
            return []
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse ruff output: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Error running ruff: {e}")
            return []

    def _extract_import_name(self, message: str) -> str:
        """
        Extract import name from ruff message.

        Examples:
            "`os` imported but unused" -> "os"
            "`typing.Dict` imported but unused" -> "typing.Dict"

        Args:
            message: Ruff error message

        Returns:
            Import name or "unknown"
        """
        # Match pattern: `name` or `module.name`
        match = re.search(r'`([^`]+)`', message)
        if match:
            return match.group(1)
        return "unknown"

    def _get_file_context(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Get graph context for a file.

        Args:
            file_path: Relative file path

        Returns:
            Dictionary with file metadata or None
        """
        query = """
        MATCH (f:File {filePath: $file_path})
        RETURN f.loc as loc,
               f.complexity as complexity,
               f.language as language
        LIMIT 1
        """

        try:
            results = self.db.execute_query(query, {"file_path": file_path})
            if results:
                return results[0]
        except Exception as e:
            self.logger.warning(f"Could not fetch graph context for {file_path}: {e}")

        return None

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity
