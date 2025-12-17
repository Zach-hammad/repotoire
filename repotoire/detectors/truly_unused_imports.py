"""
Truly Unused Imports Detector.

Detects imports that are never used in any execution path, going beyond
traditional linters which only check syntactic usage.

This detector uses graph analysis to trace call chains and determine if
imported modules are actually invoked anywhere in the code.

Addresses: FAL-114
"""

from typing import List, Dict, Any, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class TrulyUnusedImportsDetector(CodeSmellDetector):
    """Detect imports never used in execution paths."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None):
        super().__init__(neo4j_client)
        config = detector_config or {}
        self.max_call_depth = config.get("max_call_depth", 3)
        self.logger = get_logger(__name__)

    def detect(self) -> List[Finding]:
        """
        Detect truly unused imports using graph analysis.

        Uses multi-step approach to avoid Neo4j query optimization issues:
        1. Fetch all imports
        2. Check usage for each import type
        3. Filter in Python code

        Returns:
            List of Finding objects for imports never used in execution paths.
        """
        # Step 1: Get all imports from non-test files
        imports_query = """
        MATCH (f:File)-[imp:IMPORTS]->(m)
        WHERE (m:Module OR m:Class OR m:Function)
          AND NOT (f.filePath STARTS WITH 'tests/fixtures/' OR f.filePath CONTAINS '/tests/fixtures/')
          AND NOT (f.filePath STARTS WITH 'examples/' OR f.filePath CONTAINS '/examples/')
          AND NOT (f.filePath STARTS WITH 'test_fixtures/' OR f.filePath CONTAINS '/test_fixtures/')
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
        unused_imports = []
        for imp in all_imports:
            if self._is_import_used(imp):
                continue
            unused_imports.append(imp)

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
