"""
Truly Unused Imports Detector.

Detects imports that are never used in any execution path, going beyond
traditional linters which only check syntactic usage.

This detector uses graph analysis to trace call chains and determine if
imported modules are actually invoked anywhere in the code.

Addresses: FAL-114
REPO-416: Added path cache support for O(1) reachability queries.
"""

from typing import List, Dict, Any, Optional, Set, TYPE_CHECKING
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity
from repotoire.graph import FalkorDBClient
from repotoire.logging_config import get_logger

# Try to import Rust path cache for O(1) reachability queries (REPO-416)
try:
    from repotoire_fast import PyPathCache
    _HAS_PATH_CACHE = True
except ImportError:
    _HAS_PATH_CACHE = False
    PyPathCache = None  # type: ignore

if TYPE_CHECKING:
    from repotoire_fast import PyPathCache


class TrulyUnusedImportsDetector(CodeSmellDetector):
    """Detect imports never used in execution paths.

    REPO-416: Uses path cache for O(1) reachability queries when available,
    providing 100-1000x speedup over Cypher queries.
    """

    def __init__(self, graph_client: FalkorDBClient, detector_config: Optional[Dict[str, Any]] = None):
        super().__init__(graph_client)
        config = detector_config or {}
        self.max_call_depth = config.get("max_call_depth", 3)
        self.logger = get_logger(__name__)

        # Path cache for O(1) reachability queries (REPO-416)
        self.path_cache: Optional["PyPathCache"] = config.get("path_cache")

    def detect(self) -> List[Finding]:
        """
        Detect truly unused imports using graph analysis.

        Uses multi-step approach to avoid Neo4j query optimization issues:
        1. Fetch all imports
        2. Check usage for each import type (using path cache if available)
        3. Filter in Python code

        REPO-416: Uses path cache for O(1) reachability when available.

        Returns:
            List of Finding objects for imports never used in execution paths.
        """
        # Step 1: Get all imports from non-test files
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        imports_query = """
        MATCH (f:File)-[imp:IMPORTS]->(m)
        WHERE ('Module' IN labels(m) OR 'Class' IN labels(m) OR 'Function' IN labels(m))
        RETURN DISTINCT f.filePath as file_path,
               elementId(f) as file_id,
               m.qualifiedName as import_qname,
               m.name as import_name,
               labels(m)[0] as import_type,
               elementId(m) as module_id
        ORDER BY f.filePath, m.name
        """

        try:
            all_imports = self.db.execute_query(imports_query)
        except Exception as e:
            self.logger.error(f"Error fetching imports: {e}")
            return []

        self.logger.info(f"Checking {len(all_imports)} imports for usage...")

        # Step 2: Check each import for usage
        # REPO-416: Try path cache first for O(1) reachability (100-1000x faster)
        if self.path_cache is not None and _HAS_PATH_CACHE:
            try:
                unused_imports = self._find_unused_imports_with_cache(all_imports)
            except Exception as e:
                self.logger.warning(f"Path cache detection failed: {e}, using Cypher fallback")
                unused_imports = self._find_unused_imports_cypher(all_imports)
        else:
            unused_imports = self._find_unused_imports_cypher(all_imports)

        self.logger.info(f"Found {len(unused_imports)} truly unused imports")

        # Step 3: Group by file for better reporting
        imports_by_file = {}
        for result in unused_imports:
            file_path = result["file_path"]
            if file_path not in imports_by_file:
                imports_by_file[file_path] = []
            imports_by_file[file_path].append({
                "qualified_name": result["import_qname"],
                "name": result["import_name"],
                "type": result["import_type"],
            })

        findings = []
        for file_path, unused_imports_list in imports_by_file.items():

            import_list = "\n".join([
                f"  • {imp['name']} ({imp['type']})"
                for imp in unused_imports_list
            ])

            # Determine severity based on number of unused imports
            count = len(unused_imports_list)
            if count >= 5:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Create suggested fixes
            suggestions = []
            for imp in unused_imports_list:
                if imp["type"] == "Module":
                    suggestions.append(
                        f"Remove: import {imp['name']} (never called in execution paths)"
                    )
                else:
                    suggestions.append(
                        f"Remove: from ... import {imp['name']} (never used)"
                    )

            suggestion_text = "\n".join(suggestions[:5])
            if len(suggestions) > 5:
                suggestion_text += f"\n... and {len(suggestions) - 5} more"

            # Removing unused imports is quick
            estimated_effort = "Small (5-15 minutes)"

            finding = Finding(
                id=f"truly_unused_imports_{file_path.replace('/', '_')}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Truly Unused Imports in {file_path.split('/')[-1]}",
                description=(
                    f"File '{file_path}' has {count} import(s) that are never used in any "
                    f"execution path (up to {self.max_call_depth} levels deep in the call graph):\n\n"
                    f"{import_list}\n\n"
                    f"Unlike traditional linters that check syntactic usage, this detector "
                    f"uses graph analysis to verify that imports are actually invoked. "
                    f"These imports may be referenced in code but are never executed."
                ),
                affected_nodes=[imp["qualified_name"] for imp in unused_imports_list],
                affected_files=[file_path],
                suggested_fix=suggestion_text,
                estimated_effort=estimated_effort,
                graph_context={
                    "unused_imports": unused_imports_list,
                    "count": count,
                    "max_call_depth": self.max_call_depth,
                },
            )
            findings.append(finding)

        self.logger.info(
            f"TrulyUnusedImportsDetector found {len(findings)} files with truly unused imports"
        )
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity

    def _find_unused_imports_with_cache(self, all_imports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Find unused imports using path cache for O(1) reachability.

        REPO-416: This is 100-1000x faster than Cypher queries.

        Args:
            all_imports: List of import dicts from graph query

        Returns:
            List of unused import dicts
        """
        self.logger.info("Using path_cache for unused imports detection (O(1) reachability)")
        unused_imports = []

        # Precompute reachable sets from all functions/classes in each file
        # This avoids repeated lookups
        file_reachable_cache: Dict[str, Set[str]] = {}

        for imp in all_imports:
            file_path = imp["file_path"]
            import_qname = imp["import_qname"]

            # Check if import is reachable from any entity in the importing file
            if file_path not in file_reachable_cache:
                # Build reachable set for this file
                reachable: Set[str] = set()

                # Get all functions and classes in this file
                file_entities_query = """
                MATCH (f:File {filePath: $file_path})-[:CONTAINS*]->(entity)
                WHERE entity:Function OR entity:Class
                RETURN entity.qualifiedName AS qname
                """
                try:
                    entities = self.db.execute_query(file_entities_query, {"file_path": file_path})
                    for entity in entities:
                        qname = entity.get("qname")
                        if not qname:
                            continue

                        entity_id = self.path_cache.get_id(qname)
                        if entity_id is None:
                            continue

                        # Get all reachable from this entity via CALLS
                        try:
                            calls_reachable = self.path_cache.reachable_from("CALLS", entity_id)
                            for rid in calls_reachable or []:
                                rname = self.path_cache.get_name(rid)
                                if rname:
                                    reachable.add(rname)
                        except Exception as e:
                            self.logger.debug(f"Error getting CALLS reachable for {entity_id}: {e}")

                        # Get all reachable via USES (direct usage)
                        try:
                            uses_reachable = self.path_cache.reachable_from("USES", entity_id) if hasattr(self.path_cache, 'reachable_from') else set()
                            for rid in uses_reachable or []:
                                rname = self.path_cache.get_name(rid)
                                if rname:
                                    reachable.add(rname)
                        except Exception as e:
                            self.logger.debug(f"Error getting USES reachable for {entity_id}: {e}")

                        # Get all reachable via INHERITS
                        try:
                            inherits_reachable = self.path_cache.reachable_from("INHERITS", entity_id) if hasattr(self.path_cache, 'reachable_from') else set()
                            for rid in inherits_reachable or []:
                                rname = self.path_cache.get_name(rid)
                                if rname:
                                    reachable.add(rname)
                        except Exception as e:
                            self.logger.debug(f"Error getting INHERITS reachable for {entity_id}: {e}")

                except Exception as e:
                    self.logger.debug(f"Error building reachable set for {file_path}: {e}")

                file_reachable_cache[file_path] = reachable

            # Check if this import is in the reachable set
            reachable_set = file_reachable_cache.get(file_path, set())
            if import_qname not in reachable_set:
                # Also check if any parent of the import is reachable
                # (e.g., import foo, use foo.bar)
                import_parts = import_qname.split(".")
                found = False
                for i in range(len(import_parts)):
                    partial = ".".join(import_parts[:i+1])
                    if partial in reachable_set:
                        found = True
                        break
                if not found:
                    unused_imports.append(imp)

        self.logger.info(f"Path cache found {len(unused_imports)} unused imports")
        return unused_imports

    def _find_unused_imports_cypher(self, all_imports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Find unused imports using Cypher queries (fallback method).

        Args:
            all_imports: List of import dicts from graph query

        Returns:
            List of unused import dicts
        """
        unused_imports = []
        for imp in all_imports:
            if self._is_import_used(imp):
                continue
            unused_imports.append(imp)
        return unused_imports

    def _is_import_used(self, imp: Dict[str, Any]) -> bool:
        """
        Check if an import is used in call chains, directly, via inheritance, or in decorators.

        Args:
            imp: Import dict with file_id, module_id, import_name

        Returns:
            True if import is used, False if unused
        """
        file_id = imp["file_id"]
        module_id = imp["module_id"]
        import_name = imp["import_name"]

        # Check 1: Used in call chains
        call_chain_query = f"""
        MATCH (f)-[:CONTAINS*]->(func:Function)
        WHERE elementId(f) = $file_id
        MATCH path = (func)-[:CALLS*1..{self.max_call_depth}]->()-[:CONTAINS*0..1]-(m)
        WHERE elementId(m) = $module_id
        RETURN 1 AS used LIMIT 1
        """

        results = self.db.execute_query(call_chain_query, {"file_id": file_id, "module_id": module_id})
        if results:
            return True

        # Check 2: Used directly
        direct_use_query = """
        MATCH (f)-[:CONTAINS*]->(func:Function)
        WHERE elementId(f) = $file_id
        MATCH (func)-[:USES]->(m)
        WHERE elementId(m) = $module_id
        RETURN 1 AS used LIMIT 1
        """

        results = self.db.execute_query(direct_use_query, {"file_id": file_id, "module_id": module_id})
        if results:
            return True

        # Check 3: Used via inheritance
        inheritance_query = """
        MATCH (f)-[:CONTAINS*]->(c:Class)
        WHERE elementId(f) = $file_id
        MATCH (c)-[:INHERITS]->(m)
        WHERE elementId(m) = $module_id
        RETURN 1 AS used LIMIT 1
        """

        results = self.db.execute_query(inheritance_query, {"file_id": file_id, "module_id": module_id})
        if results:
            return True

        # Check 4: Used in decorators
        decorator_query = """
        MATCH (f)-[:CONTAINS*]->(node)
        WHERE elementId(f) = $file_id
          AND (node:Function OR node:Class)
          AND node.decorators IS NOT NULL
          AND ANY(decorator IN node.decorators WHERE decorator STARTS WITH $import_name + '.')
        RETURN 1 AS used LIMIT 1
        """

        results = self.db.execute_query(
            decorator_query,
            {"file_id": file_id, "import_name": import_name}
        )
        if results:
            return True

        # Not used anywhere
        return False
