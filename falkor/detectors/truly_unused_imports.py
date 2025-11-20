"""
Truly Unused Imports Detector.

Detects imports that are never used in any execution path, going beyond
traditional linters which only check syntactic usage.

This detector uses graph analysis to trace call chains and determine if
imported modules are actually invoked anywhere in the code.

Addresses: FAL-114
"""

from typing import List, Dict, Any, Optional
from falkor.detectors.base import CodeSmellDetector
from falkor.models import Finding, Severity
from falkor.graph.client import Neo4jClient
from falkor.logging_config import get_logger


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

        Returns:
            List of Finding objects for imports never used in execution paths.
        """
        # Note: This query looks for imports that are never used in call chains
        # Traditional linters check syntactic usage; this checks semantic usage
        # Uses Neo4j 5.x EXISTS {} pattern matching for better performance
        # Note: max_depth must be hardcoded in the query (can't use parameters in MATCH patterns)
        query = f"""
        // Find all import relationships
        MATCH (f:File)-[imp:IMPORTS]->(m)
        WHERE m:Module OR m:Class OR m:Function

        // Check if the imported entity is NOT used in call chains
        AND NOT EXISTS {{
            // Check if any function in the file calls into the imported module
            MATCH (f)-[:CONTAINS*]->(func:Function)
            MATCH path = (func)-[:CALLS*1..{self.max_call_depth}]->()-[:CONTAINS*0..1]-(m)
            RETURN path LIMIT 1
        }}
        // Check if the imported entity is NOT used directly
        AND NOT EXISTS {{
            MATCH (f)-[:CONTAINS*]->(func:Function)
            MATCH (func)-[:USES]->(m)
        }}
        // Check if the imported entity is NOT inherited from
        AND NOT EXISTS {{
            MATCH (f)-[:CONTAINS*]->(c:Class)
            MATCH (c)-[:INHERITS]->(m)
        }}

        RETURN DISTINCT f.filePath as file_path,
               m.qualifiedName as unused_import,
               m.name as import_name,
               labels(m)[0] as import_type
        ORDER BY f.filePath, unused_import
        LIMIT 100
        """

        try:
            results = self.db.execute_query(query)
        except Exception as e:
            self.logger.error(f"Error executing Truly Unused Imports detection query: {e}")
            return []

        # Group by file for better reporting
        imports_by_file = {}
        for result in results:
            file_path = result["file_path"]
            if file_path not in imports_by_file:
                imports_by_file[file_path] = []
            imports_by_file[file_path].append({
                "qualified_name": result["unused_import"],
                "name": result["import_name"],
                "type": result["import_type"],
            })

        findings = []
        for file_path, unused_imports in imports_by_file.items():
            import_list = "\n".join([
                f"  • {imp['name']} ({imp['type']})"
                for imp in unused_imports
            ])

            # Determine severity based on number of unused imports
            count = len(unused_imports)
            if count >= 5:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Create suggested fixes
            suggestions = []
            for imp in unused_imports:
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
                affected_nodes=[imp["qualified_name"] for imp in unused_imports],
                affected_files=[file_path],
                suggested_fix=suggestion_text,
                graph_context={
                    "unused_imports": unused_imports,
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
