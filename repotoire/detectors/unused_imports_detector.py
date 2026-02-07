"""Unused imports detector - fast graph-based alternative to pylint.

Detects imports that are never referenced in the codebase, indicating
dead code that should be cleaned up.
"""

from typing import Any, Dict, List, Optional, Set

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity


class UnusedImportsDetector(CodeSmellDetector):
    """Detects imports that are never used.

    An unused import is one that appears in an IMPORTS relationship
    but is never referenced via:
    - Function calls (CALLS)
    - Class inheritance (INHERITS)
    - Attribute access
    - Type annotations

    This is a fast graph-based alternative to running pylint's
    unused-import check, leveraging the pre-built code graph.
    """

    # Common imports that are often used implicitly (side effects, type checking)
    DEFAULT_IGNORE_PATTERNS = [
        "__future__",  # Future imports for compatibility
        "typing",  # Often used in TYPE_CHECKING blocks
        "typing_extensions",
        "__init__",  # Package init modules
        "annotations",  # from __future__ import annotations
    ]

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize unused imports detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.ignore_patterns = config.get("ignore_patterns", self.DEFAULT_IGNORE_PATTERNS)
        self.max_findings = config.get("max_findings", 100)

    def detect(self) -> List[Finding]:
        """Detect unused imports in the codebase.

        Returns:
            List of findings for unused imports
        """
        # Fast path: use QueryCache if available
        if self.query_cache is not None:
            self.logger.debug("Using QueryCache for unused imports detection")
            return self._detect_cached()

        return self._detect_from_graph()

    def _detect_cached(self) -> List[Finding]:
        """Detect unused imports using QueryCache.

        O(1) lookup from prefetched data instead of database queries.

        Returns:
            List of findings for unused imports
        """
        findings = []

        # Build set of all referenced entities
        referenced: Set[str] = set()

        # Add all called functions (they're used)
        for callees in self.query_cache.calls.values():
            referenced.update(callees)

        # Add all inherited classes (they're used)
        for parents in self.query_cache.inherits.values():
            referenced.update(parents)

        # Add all callers (the function itself is defined and possibly referenced)
        referenced.update(self.query_cache.called_by.keys())

        # Add all classes that have children (they're used)
        referenced.update(self.query_cache.inherited_by.keys())

        # Check each import
        for importer, imported_set in self.query_cache.imports.items():
            for imported in imported_set:
                # Skip ignored patterns
                if self._should_ignore(imported):
                    continue

                # Check if imported module is referenced
                if not self._is_referenced(imported, referenced):
                    # Get file info for the importer
                    file_data = self.query_cache.files.get(importer)
                    file_path = file_data.file_path if file_data else importer

                    finding = self._create_finding(
                        imported_name=imported,
                        importer_file=importer,
                        file_path=file_path,
                    )
                    findings.append(finding)

                    if len(findings) >= self.max_findings:
                        break

            if len(findings) >= self.max_findings:
                break

        # Sort by file path for consistent output
        findings.sort(key=lambda f: (f.affected_files[0] if f.affected_files else "", f.title))

        self.logger.info(f"UnusedImportsDetector (cached) found {len(findings)} unused imports")
        return findings

    def _detect_from_graph(self) -> List[Finding]:
        """Detect unused imports via graph queries.

        Falls back to direct database queries when QueryCache is not available.

        Returns:
            List of findings for unused imports
        """
        # REPO-600: Filter by tenant_id AND repo_id
        isolation_filter = self._get_isolation_filter("imp")

        # Query: Find imports that have no references
        # An import is "used" if:
        # 1. Something CALLS a function within the imported module
        # 2. A class INHERITS from a class in the imported module
        # 3. The imported name appears in REFERENCES relationships (if available)
        query = f"""
        // Get all imports
        MATCH (importer)-[:IMPORTS]->(imported)
        WHERE importer.qualifiedName IS NOT NULL
          AND imported.qualifiedName IS NOT NULL
          {isolation_filter.replace('imp', 'importer')}

        // Check if the imported module/entity is referenced
        OPTIONAL MATCH (caller)-[:CALLS]->(imported)
        OPTIONAL MATCH (child:Class)-[:INHERITS]->(imported)
        OPTIONAL MATCH ()-[:CALLS]->(fn:Function)
        WHERE fn.qualifiedName STARTS WITH imported.qualifiedName + '.'

        WITH importer, imported,
             count(caller) AS call_count,
             count(child) AS inherit_count,
             count(fn) AS nested_call_count

        // Filter to unused imports (no references)
        WHERE call_count = 0 AND inherit_count = 0 AND nested_call_count = 0

        // Get file info
        OPTIONAL MATCH (importer)<-[:CONTAINS*]-(f:File)

        RETURN 
            imported.qualifiedName AS imported_name,
            importer.qualifiedName AS importer_name,
            imported.name AS simple_name,
            coalesce(f.filePath, importer.filePath, importer.qualifiedName) AS file_path,
            imported.lineStart AS line_start
        ORDER BY file_path, imported_name
        LIMIT $max_findings
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(max_findings=self.max_findings),
            )
        except Exception as e:
            self.logger.error(f"Error executing unused imports query: {e}")
            # Try simpler fallback query
            return self._detect_simple_fallback()

        findings = []
        for row in results:
            imported_name = row.get("imported_name", "")

            # Skip ignored patterns
            if self._should_ignore(imported_name):
                continue

            finding = self._create_finding(
                imported_name=imported_name,
                importer_file=row.get("importer_name", ""),
                file_path=row.get("file_path", "unknown"),
                line_start=row.get("line_start"),
            )
            findings.append(finding)

        self.logger.info(f"UnusedImportsDetector found {len(findings)} unused imports")
        return findings

    def _detect_simple_fallback(self) -> List[Finding]:
        """Simple fallback detection when complex query fails.

        Uses a simpler approach: get all imports and all called/inherited entities,
        then compute the difference.

        Returns:
            List of findings for unused imports
        """
        self.logger.debug("Using simple fallback for unused imports detection")

        isolation_filter = self._get_isolation_filter("n")

        # Get all imports
        imports_query = f"""
        MATCH (importer)-[:IMPORTS]->(imported)
        WHERE importer.qualifiedName IS NOT NULL
          AND imported.qualifiedName IS NOT NULL
          {isolation_filter.replace('n', 'importer')}
        OPTIONAL MATCH (importer)<-[:CONTAINS*]-(f:File)
        RETURN 
            imported.qualifiedName AS imported_name,
            importer.qualifiedName AS importer_name,
            coalesce(f.filePath, importer.filePath) AS file_path
        """

        # Get all referenced entities (called or inherited)
        refs_query = f"""
        MATCH ()-[r:CALLS|INHERITS]->(target)
        WHERE target.qualifiedName IS NOT NULL
        RETURN DISTINCT target.qualifiedName AS ref_name
        """

        try:
            imports_results = self.db.execute_query(
                imports_query, self._get_query_params()
            )
            refs_results = self.db.execute_query(refs_query, self._get_query_params())
        except Exception as e:
            self.logger.error(f"Fallback query failed: {e}")
            return []

        # Build set of referenced names
        referenced: Set[str] = set()
        for row in refs_results:
            ref_name = row.get("ref_name")
            if ref_name:
                referenced.add(ref_name)

        # Find unused imports
        findings = []
        for row in imports_results:
            imported_name = row.get("imported_name", "")

            if self._should_ignore(imported_name):
                continue

            if not self._is_referenced(imported_name, referenced):
                finding = self._create_finding(
                    imported_name=imported_name,
                    importer_file=row.get("importer_name", ""),
                    file_path=row.get("file_path", "unknown"),
                )
                findings.append(finding)

                if len(findings) >= self.max_findings:
                    break

        return findings

    def _should_ignore(self, name: str) -> bool:
        """Check if import should be ignored.

        Args:
            name: Qualified name of the import

        Returns:
            True if import should be ignored
        """
        if not name:
            return True

        name_lower = name.lower()
        for pattern in self.ignore_patterns:
            if pattern.lower() in name_lower:
                return True

        return False

    def _is_referenced(self, imported: str, referenced: Set[str]) -> bool:
        """Check if an imported module is referenced.

        Handles both exact matches and prefix matches (for module imports
        where we use a submodule/function).

        Args:
            imported: The imported module/entity qualified name
            referenced: Set of all referenced qualified names

        Returns:
            True if the import is used somewhere
        """
        # Exact match
        if imported in referenced:
            return True

        # Check if any referenced entity starts with this import
        # (e.g., import foo, use foo.bar)
        for ref in referenced:
            if ref.startswith(imported + "."):
                return True

        # Check if this import is a sub-path of something referenced
        # (e.g., from foo.bar import baz, and foo.bar is referenced)
        parts = imported.split(".")
        for i in range(1, len(parts)):
            prefix = ".".join(parts[:i])
            if prefix in referenced:
                return True

        return False

    def _create_finding(
        self,
        imported_name: str,
        importer_file: str,
        file_path: str,
        line_start: Optional[int] = None,
    ) -> Finding:
        """Create a finding for an unused import.

        Args:
            imported_name: Qualified name of the unused import
            importer_file: File that contains the import
            file_path: Path to the source file
            line_start: Optional line number of the import

        Returns:
            Finding object
        """
        simple_name = imported_name.split(".")[-1]

        description = (
            f"Import '{imported_name}' is not used in '{file_path}'. "
            f"Unused imports add clutter and can slow down module loading."
        )

        recommendation = (
            f"Remove the unused import:\n"
            f"  - Delete the import statement for '{simple_name}'\n"
            f"  - Or if it's needed for type checking, use:\n"
            f"    if TYPE_CHECKING:\n"
            f"        from ... import {simple_name}"
        )

        finding = Finding(
            id=f"unused_import_{importer_file}_{imported_name}".replace(".", "_"),
            detector="UnusedImportsDetector",
            severity=Severity.LOW,
            title=f"Unused import: {simple_name}",
            description=description,
            affected_nodes=[imported_name],
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            suggested_fix=recommendation,
            estimated_effort="Trivial (1-5 minutes)",
            graph_context={
                "imported_name": imported_name,
                "importer": importer_file,
            },
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="UnusedImportsDetector",
            confidence=0.85,  # High confidence - graph-based detection is reliable
            evidence=["no_calls", "no_inheritance", "no_references"],
            tags=["unused_import", "dead_code", "cleanup"],
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (always LOW for unused imports).

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        return Severity.LOW
