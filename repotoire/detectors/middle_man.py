"""
Middle Man Detector.

Detects classes that mostly delegate to other classes without adding value,
indicating unnecessary indirection.

Traditional linters cannot detect this pattern as it requires analyzing
method call patterns across classes.

Addresses: FAL-112
"""

import json
from typing import Any, Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity


class MiddleManDetector(CodeSmellDetector):
    """Detect classes that mostly delegate to other classes."""

    def __init__(self, graph_client: FalkorDBClient, detector_config: Optional[Dict[str, Any]] = None, enricher: Optional[GraphEnricher] = None):
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        config = detector_config or {}
        self.min_delegation_methods = config.get("min_delegation_methods", 3)
        self.delegation_threshold = config.get("delegation_threshold", 0.7)
        self.max_complexity = config.get("max_complexity", 2)
        self.logger = get_logger(__name__)

    def detect(self) -> List[Finding]:
        """
        Detect middle man classes using graph analysis.

        Returns:
            List of Finding objects for classes that mostly delegate.
        """
        # Fast path: use QueryCache if available
        if self.query_cache is not None:
            self.logger.debug("Using QueryCache for middle man detection")
            return self._detect_cached()

        # REPO-600: Filter by tenant_id AND repo_id for defense-in-depth isolation
        repo_filter = self._get_isolation_filter("c")
        query = f"""
        // First count total methods per class
        MATCH (c:Class)-[:CONTAINS]->(all_m:Function)
        WHERE all_m.is_method = true {repo_filter}
        WITH c, count(all_m) as total_methods
        WHERE total_methods > 0

        // Find delegation patterns
        MATCH (c)-[:CONTAINS]->(m:Function)
        WHERE m.is_method = true
          AND (m.complexity IS NULL OR m.complexity <= $max_complexity)
        MATCH (m)-[:CALLS]->(delegated:Function)
        MATCH (delegated)<-[:CONTAINS]-(target:Class)
        WHERE c <> target

        WITH c, target, total_methods,
             count(DISTINCT m) as delegation_count

        // Filter based on thresholds
        WHERE delegation_count >= $min_delegation_methods
          AND CAST(delegation_count AS DOUBLE) / total_methods >= $delegation_threshold

        RETURN c.qualifiedName as middle_man,
               c.name as class_name,
               c.filePath as file_path,
               c.lineStart as line_start,
               c.lineEnd as line_end,
               target.qualifiedName as delegates_to,
               target.name as target_name,
               delegation_count,
               total_methods,
               CAST(delegation_count * 100 AS DOUBLE) / total_methods as delegation_percentage
        ORDER BY delegation_percentage DESC
        LIMIT 50
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(
                    min_delegation_methods=self.min_delegation_methods,
                    delegation_threshold=self.delegation_threshold,
                    max_complexity=self.max_complexity,
                ),
            )
        except Exception as e:
            self.logger.error(f"Error executing Middle Man detection query: {e}")
            return []

        findings = []
        for result in results:
            delegation_pct = result["delegation_percentage"]

            # Determine severity based on delegation percentage
            if delegation_pct >= 90:
                severity = Severity.HIGH
            elif delegation_pct >= 70:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Create contextual suggested fix
            if delegation_pct >= 90:
                suggestion = (
                    f"Class '{result['class_name']}' delegates {delegation_pct:.0f}% of methods "
                    f"({result['delegation_count']}/{result['total_methods']}) to '{result['target_name']}'. "
                    f"Consider:\n"
                    f"  1. Remove the middle man and use '{result['target_name']}' directly\n"
                    f"  2. If this is a facade, add value by combining operations\n"
                    f"  3. Document the architectural reason if delegation is intentional"
                )
            else:
                suggestion = (
                    f"Class '{result['class_name']}' delegates {delegation_pct:.0f}% of methods "
                    f"to '{result['target_name']}'. Consider whether this indirection adds value."
                )

            # Estimate effort - removing a middle man is usually straightforward
            if severity == Severity.HIGH:
                estimated_effort = "Medium (1-2 hours)"
            elif severity == Severity.MEDIUM:
                estimated_effort = "Small (30-60 minutes)"
            else:
                estimated_effort = "Small (15-30 minutes)"

            finding = Finding(
                id=f"middle_man_{result['middle_man']}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Middle Man: {result['class_name']}",
                description=(
                    f"Class '{result['class_name']}' acts as a middle man, delegating "
                    f"{result['delegation_count']} out of {result['total_methods']} methods "
                    f"({delegation_pct:.0f}%) to '{result['target_name']}' without adding significant value.\n\n"
                    f"This pattern adds unnecessary indirection and increases maintenance burden. "
                    f"Simple delegation methods with low complexity suggest the class may not be needed."
                ),
                affected_nodes=[result["middle_man"]],
                affected_files=[result["file_path"]],
                line_start=result.get("line_start"),
                line_end=result.get("line_end"),
                suggested_fix=suggestion,
                estimated_effort=estimated_effort,
                graph_context={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in {
                    "delegation_count": result["delegation_count"],
                    "total_methods": result["total_methods"],
                    "delegation_percentage": delegation_pct,
                    "delegates_to": result["delegates_to"],
                    "target_name": result["target_name"],
                }.items()},
            )
            # Add collaboration metadata (REPO-150 Phase 1)
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="MiddleManDetector",
                confidence=0.8,
                evidence=['delegation_only'],
                tags=['middle_man', 'code_quality', 'maintenance']
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher and finding.affected_nodes:
                for entity_qname in finding.affected_nodes:
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=entity_qname,
                            detector="MiddleManDetector",
                            severity=finding.severity.value,
                            issues=['delegation_only'],
                            confidence=0.8,
                            metadata={k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in (finding.graph_context or {}).items()}
                        )
                    except Exception:
                        pass


            findings.append(finding)

        self.logger.info(
            f"MiddleManDetector found {len(findings)} classes acting as middle men"
        )
        return findings

    def _detect_cached(self) -> List[Finding]:
        """Detect middle man classes using QueryCache.

        O(1) lookup from prefetched data instead of database query.

        Returns:
            List of Finding objects for classes that mostly delegate.
        """
        findings = []
        
        # Track delegation patterns: class -> {target_class -> delegating_method_count}
        for class_name, class_data in self.query_cache.classes.items():
            # Get all methods for this class
            methods = self.query_cache.get_methods(class_name)
            total_methods = len(methods)
            
            if total_methods == 0:
                continue
            
            # Track delegation to other classes
            delegation_targets: Dict[str, int] = {}
            
            for method_name in methods:
                func_data = self.query_cache.get_function(method_name)
                if not func_data:
                    continue
                
                # Skip complex methods (they do more than just delegate)
                if func_data.complexity is not None and func_data.complexity > self.max_complexity:
                    continue
                
                # Get methods this method calls
                callees = self.query_cache.get_callees(method_name)
                
                for callee_name in callees:
                    # Find which class the callee belongs to
                    target_class = self.query_cache.get_parent_class(callee_name)
                    if target_class:
                        # Only count delegation to OTHER classes
                        if target_class != class_name:
                            delegation_targets[target_class] = delegation_targets.get(target_class, 0) + 1
            
            # Find the primary delegation target (if any)
            if not delegation_targets:
                continue
            
            # Get the class we delegate to most
            target_class = max(delegation_targets.keys(), key=lambda k: delegation_targets[k])
            delegation_count = delegation_targets[target_class]
            
            # Check thresholds
            if delegation_count < self.min_delegation_methods:
                continue
            
            delegation_ratio = delegation_count / total_methods
            if delegation_ratio < self.delegation_threshold:
                continue
            
            delegation_pct = delegation_ratio * 100
            
            # Get target class data for display name
            target_data = self.query_cache.classes.get(target_class)
            target_name = target_class.split(".")[-1] if target_class else "unknown"
            simple_name = class_name.split(".")[-1]
            
            # Determine severity based on delegation percentage
            if delegation_pct >= 90:
                severity = Severity.HIGH
            elif delegation_pct >= 70:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW
            
            # Create contextual suggested fix
            if delegation_pct >= 90:
                suggestion = (
                    f"Class '{simple_name}' delegates {delegation_pct:.0f}% of methods "
                    f"({delegation_count}/{total_methods}) to '{target_name}'. "
                    f"Consider:\n"
                    f"  1. Remove the middle man and use '{target_name}' directly\n"
                    f"  2. If this is a facade, add value by combining operations\n"
                    f"  3. Document the architectural reason if delegation is intentional"
                )
            else:
                suggestion = (
                    f"Class '{simple_name}' delegates {delegation_pct:.0f}% of methods "
                    f"to '{target_name}'. Consider whether this indirection adds value."
                )
            
            # Estimate effort
            if severity == Severity.HIGH:
                estimated_effort = "Medium (1-2 hours)"
            elif severity == Severity.MEDIUM:
                estimated_effort = "Small (30-60 minutes)"
            else:
                estimated_effort = "Small (15-30 minutes)"
            
            finding = Finding(
                id=f"middle_man_{class_name}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Middle Man: {simple_name}",
                description=(
                    f"Class '{simple_name}' acts as a middle man, delegating "
                    f"{delegation_count} out of {total_methods} methods "
                    f"({delegation_pct:.0f}%) to '{target_name}' without adding significant value.\n\n"
                    f"This pattern adds unnecessary indirection and increases maintenance burden. "
                    f"Simple delegation methods with low complexity suggest the class may not be needed."
                ),
                affected_nodes=[class_name],
                affected_files=[class_data.file_path] if class_data.file_path else [],
                line_start=class_data.line_start,
                line_end=class_data.line_end,
                suggested_fix=suggestion,
                estimated_effort=estimated_effort,
                graph_context={
                    "delegation_count": delegation_count,
                    "total_methods": total_methods,
                    "delegation_percentage": delegation_pct,
                    "delegates_to": target_class,
                    "target_name": target_name,
                },
            )
            
            # Add collaboration metadata
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="MiddleManDetector",
                confidence=0.8,
                evidence=['delegation_only'],
                tags=['middle_man', 'code_quality', 'maintenance']
            ))
            
            # Flag entity in graph for cross-detector collaboration
            if self.enricher:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=class_name,
                        detector="MiddleManDetector",
                        severity=finding.severity.value,
                        issues=['delegation_only'],
                        confidence=0.8,
                        metadata={
                            "delegation_count": delegation_count,
                            "total_methods": total_methods,
                            "delegation_percentage": delegation_pct,
                        }
                    )
                except Exception:
                    pass
            
            findings.append(finding)
        
        # Sort by delegation percentage descending
        findings.sort(key=lambda f: f.graph_context.get("delegation_percentage", 0), reverse=True)
        
        # Limit to 50
        findings = findings[:50]
        
        self.logger.info(f"MiddleManDetector (cached) found {len(findings)} classes acting as middle men")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity
