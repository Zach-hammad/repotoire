"""Rust-based graph detectors (REPO-433).

High-performance graph-based code smell detectors implemented in Rust.
These detectors leverage the repotoire_fast Rust library for 10-100x speedups
over pure Cypher query implementations.

DETECTORS:
- PackageStabilityDetector: Robert Martin's package metrics (I, A, D)
- TechnicalDebtHotspotDetector: Churn × Complexity / Health hotspot analysis
- LayeredArchitectureDetector: Back-call and skip-call detection
- CallChainDepthDetector: Deep call chain and bottleneck detection
- HubDependencyDetector: Architectural hub detection via centrality
- ChangeCouplingDetector: Temporal coupling from git history

ACADEMIC REFERENCES:
- Martin, R. "Agile Software Development" (2002) - Package stability metrics
- Tornhill, A. "Your Code as a Crime Scene" (2015) - Hotspot analysis
- Lippert, M. & Roock, S. "Refactoring in Large Software Projects" (2006)

REPO-416: Added path cache support for O(1) reachability queries.
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

if TYPE_CHECKING:
    from repotoire_fast import PyPathCache

# Import Rust graph detector functions
from repotoire_fast import (
    graph_package_stability,
    detect_unstable_packages,
    detect_hotspots,
    detect_layer_violations,
    detect_deep_call_chains,
    find_bottleneck_functions,
    detect_hub_dependencies,
    detect_change_coupling,
)

logger = get_logger(__name__)


class PackageStabilityDetector(CodeSmellDetector):
    """Detects packages with poor stability metrics (Robert Martin's metrics).

    Uses Martin's package principles from "Agile Software Development":
    - Instability (I) = Ce / (Ca + Ce)
    - Abstractness (A) = Na / Nc
    - Distance from Main Sequence (D) = |A + I - 1|

    Packages far from the main sequence are problematic:
    - Zone of Pain: Stable but concrete (hard to extend)
    - Zone of Uselessness: Unstable but abstract (unused abstractions)
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.distance_threshold = self.config.get("distance_threshold", 0.3)

    def detect(self) -> List[Finding]:
        """Detect packages with poor stability metrics."""
        findings: List[Finding] = []

        try:
            # Get package import edges and class counts from graph
            edges, package_names, abstract_counts = self._extract_package_data()

            if not package_names:
                logger.debug("No packages found for stability analysis")
                return findings

            # Run Rust detector
            num_packages = len(package_names)
            results = detect_unstable_packages(
                edges, num_packages, abstract_counts, self.distance_threshold
            )

            for detector, severity, message, affected_nodes, metadata in results:
                finding = self._create_finding(
                    package_names=package_names,
                    affected_ids=affected_nodes,
                    message=message,
                    severity_str=severity,
                    metadata=metadata
                )
                findings.append(finding)

            logger.info(f"PackageStabilityDetector found {len(findings)} issues")
            return findings

        except Exception as e:
            logger.error(f"Error in PackageStabilityDetector: {e}", exc_info=True)
            return findings

    def _extract_package_data(self) -> Tuple[List[Tuple[int, int]], List[str], List[Tuple[int, int]]]:
        """Extract package dependency data from FalkorDB.

        Returns:
            Tuple of (edges, package_names, abstract_counts)
        """
        repo_filter = self._get_isolation_filter("f")

        # Get all modules/packages with their import relationships
        query = f"""
        MATCH (f:File)
        WHERE true {repo_filter}
        WITH f,
             CASE WHEN f.filePath CONTAINS '/'
                  THEN split(f.filePath, '/')[..-1]
                  ELSE ['.'] END AS package_parts
        WITH f, reduce(s='', p IN package_parts | s + '/' + p) AS package_path
        RETURN DISTINCT package_path AS package, collect(f.filePath) AS files
        """

        packages = self.db.execute_query(query, self._get_query_params())

        # Build package name list and file->package mapping
        package_names: List[str] = []
        file_to_package: Dict[str, int] = {}

        for i, record in enumerate(packages):
            pkg_name = record["package"] or "."
            package_names.append(pkg_name)
            for file_path in record.get("files", []):
                file_to_package[file_path] = i

        # Get import edges between packages
        import_query = f"""
        MATCH (f1:File)-[:IMPORTS]->(f2:File)
        WHERE true {repo_filter}
        RETURN f1.filePath AS source, f2.filePath AS target
        """

        import_results = self.db.execute_query(import_query, self._get_query_params())

        edges: List[Tuple[int, int]] = []
        for record in import_results:
            src_file = record["source"]
            dst_file = record["target"]

            src_pkg = file_to_package.get(src_file)
            dst_pkg = file_to_package.get(dst_file)

            if src_pkg is not None and dst_pkg is not None and src_pkg != dst_pkg:
                edges.append((src_pkg, dst_pkg))

        # Count abstract classes per package (interfaces, abstract classes)
        abstract_query = f"""
        MATCH (c:Class)
        WHERE true {self._get_isolation_filter('c')}
        OPTIONAL MATCH (f:File)-[:CONTAINS]->(c)
        RETURN f.filePath AS file_path,
               c.isAbstract AS is_abstract
        """

        class_results = self.db.execute_query(abstract_query, self._get_query_params())

        package_abstract: Dict[int, int] = {}
        package_total: Dict[int, int] = {}

        for record in class_results:
            file_path = record.get("file_path")
            is_abstract = record.get("is_abstract", False)

            pkg_id = file_to_package.get(file_path)
            if pkg_id is not None:
                package_total[pkg_id] = package_total.get(pkg_id, 0) + 1
                if is_abstract:
                    package_abstract[pkg_id] = package_abstract.get(pkg_id, 0) + 1

        abstract_counts = [
            (package_abstract.get(i, 0), package_total.get(i, 1))
            for i in range(len(package_names))
        ]

        return edges, package_names, abstract_counts

    def _create_finding(
        self,
        package_names: List[str],
        affected_ids: List[int],
        message: str,
        severity_str: str,
        metadata: Dict[str, float]
    ) -> Finding:
        """Create a finding for an unstable package."""
        finding_id = str(uuid.uuid4())

        affected_packages = [package_names[i] for i in affected_ids if i < len(package_names)]

        severity = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
        }.get(severity_str, Severity.MEDIUM)

        return Finding(
            id=finding_id,
            detector="PackageStabilityDetector",
            severity=severity,
            title=f"Package stability issue: {affected_packages[0] if affected_packages else 'unknown'}",
            description=message,
            affected_nodes=affected_packages,
            affected_files=[],
            graph_context={
                "instability": metadata.get("instability", 0),
                "abstractness": metadata.get("abstractness", 0),
                "distance": metadata.get("distance", 0),
                "ca": int(metadata.get("ca", 0)),
                "ce": int(metadata.get("ce", 0)),
            },
            suggested_fix=self._suggest_fix(metadata),
            estimated_effort=self._estimate_effort(metadata.get("distance", 0)),
            created_at=datetime.now(),
        )

    def severity(self, finding: Finding) -> Severity:
        distance = finding.graph_context.get("distance", 0)
        if distance > 0.7:
            return Severity.CRITICAL
        elif distance > 0.5:
            return Severity.HIGH
        elif distance > 0.3:
            return Severity.MEDIUM
        return Severity.LOW

    def _suggest_fix(self, metadata: Dict[str, float]) -> str:
        instability = metadata.get("instability", 0.5)
        abstractness = metadata.get("abstractness", 0.5)

        if instability < 0.5 and abstractness < 0.5:
            return (
                "Zone of Pain: Package is too stable and concrete.\n"
                "Consider:\n"
                "1. Extract interfaces for extension points\n"
                "2. Use dependency injection for flexibility\n"
                "3. Add abstract base classes where inheritance is expected"
            )
        else:
            return (
                "Zone of Uselessness: Package is unstable but abstract.\n"
                "Consider:\n"
                "1. Remove unused abstractions\n"
                "2. Consolidate interfaces if they're not used\n"
                "3. Move concrete implementations into this package"
            )

    def _estimate_effort(self, distance: float) -> str:
        if distance > 0.7:
            return "Large (1-2 weeks)"
        elif distance > 0.5:
            return "Medium (2-4 days)"
        return "Small (1-2 days)"


class TechnicalDebtHotspotDetector(CodeSmellDetector):
    """Detects technical debt hotspots using churn × complexity analysis.

    Based on Adam Tornhill's "Your Code as a Crime Scene":
    - Hotspot Score = Churn × Complexity / Health
    - High-churn, high-complexity, low-health files are refactoring priorities
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.min_churn = self.config.get("min_churn", 5)
        self.min_complexity = self.config.get("min_complexity", 5.0)
        self.percentile_threshold = self.config.get("percentile_threshold", 20.0)

    def detect(self) -> List[Finding]:
        """Detect technical debt hotspots."""
        findings: List[Finding] = []

        try:
            # Get file metrics from graph
            file_metrics, file_paths = self._extract_file_metrics()

            if not file_metrics:
                logger.debug("No file metrics found for hotspot analysis")
                return findings

            # Run Rust detector
            hotspots = detect_hotspots(file_metrics, self.min_churn, self.min_complexity)

            for file_id, score, churn, complexity, health, percentile in hotspots:
                if percentile <= self.percentile_threshold:
                    finding = self._create_finding(
                        file_path=file_paths.get(file_id, "unknown"),
                        score=score,
                        churn=churn,
                        complexity=complexity,
                        health=health,
                        percentile=percentile
                    )
                    findings.append(finding)

            logger.info(f"TechnicalDebtHotspotDetector found {len(findings)} hotspots")
            return findings

        except Exception as e:
            logger.error(f"Error in TechnicalDebtHotspotDetector: {e}", exc_info=True)
            return findings

    def _extract_file_metrics(self) -> Tuple[List[Tuple[int, int, float, float, int]], Dict[int, str]]:
        """Extract file metrics from FalkorDB.

        Returns:
            Tuple of (file_metrics, file_paths_map)
        """
        repo_filter = self._get_isolation_filter("f")

        query = f"""
        MATCH (f:File)
        WHERE true {repo_filter}
        RETURN f.filePath AS path,
               coalesce(f.churnCount, 0) AS churn,
               coalesce(f.complexity, 0.0) AS complexity,
               coalesce(f.codeHealth, 50.0) AS health,
               coalesce(f.lineCount, 0) AS loc
        """

        results = self.db.execute_query(query, self._get_query_params())

        file_metrics: List[Tuple[int, int, float, float, int]] = []
        file_paths: Dict[int, str] = {}

        for i, record in enumerate(results):
            file_paths[i] = record["path"]
            file_metrics.append((
                i,
                int(record["churn"]),
                float(record["complexity"]),
                float(record["health"]),
                int(record["loc"])
            ))

        return file_metrics, file_paths

    def _create_finding(
        self,
        file_path: str,
        score: float,
        churn: int,
        complexity: float,
        health: float,
        percentile: float
    ) -> Finding:
        """Create a finding for a hotspot."""
        finding_id = str(uuid.uuid4())

        if percentile <= 5.0:
            severity = Severity.CRITICAL
        elif percentile <= 10.0:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        return Finding(
            id=finding_id,
            detector="TechnicalDebtHotspotDetector",
            severity=severity,
            title=f"Technical debt hotspot: {file_path.split('/')[-1]}",
            description=(
                f"File is in top {percentile:.1f}% of technical debt hotspots.\n"
                f"Hotspot score: {score:.2f}\n"
                f"Churn: {churn} changes, Complexity: {complexity:.1f}, Health: {health:.1f}"
            ),
            affected_nodes=[file_path],
            affected_files=[file_path],
            graph_context={
                "hotspot_score": score,
                "churn_count": churn,
                "complexity": complexity,
                "code_health": health,
                "percentile": percentile,
            },
            suggested_fix=self._suggest_fix(score, complexity, churn),
            estimated_effort=self._estimate_effort(score),
            created_at=datetime.now(),
        )

    def severity(self, finding: Finding) -> Severity:
        percentile = finding.graph_context.get("percentile", 50)
        if percentile <= 5.0:
            return Severity.CRITICAL
        elif percentile <= 10.0:
            return Severity.HIGH
        return Severity.MEDIUM

    def _suggest_fix(self, score: float, complexity: float, churn: int) -> str:
        suggestions = []

        if complexity > 20:
            suggestions.append("Break down complex functions into smaller, focused units")
        if churn > 50:
            suggestions.append("Stabilize the interface to reduce change frequency")

        suggestions.append("Consider refactoring to reduce coupling")
        suggestions.append("Add comprehensive tests before refactoring")

        return "\n".join(f"{i+1}. {s}" for i, s in enumerate(suggestions))

    def _estimate_effort(self, score: float) -> str:
        if score > 100:
            return "Large (1-2 weeks)"
        elif score > 50:
            return "Medium (2-4 days)"
        return "Small (1-2 days)"


class LayeredArchitectureDetector(CodeSmellDetector):
    """Detects layered architecture violations (back-calls and skip-calls).

    Identifies:
    - Back-calls: Lower layer importing from higher layer
    - Skip-calls: Layer bypassing intermediate layers
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        # Path cache for O(1) reachability queries (REPO-416)
        self.path_cache: Optional["PyPathCache"] = self.config.get("path_cache")
        # Default layer configuration (can be overridden)
        self.layers = self.config.get("layers", [
            {"name": "infrastructure", "level": 0, "patterns": ["repositories", "database", "data"]},
            {"name": "domain", "level": 1, "patterns": ["domain", "models", "entities"]},
            {"name": "application", "level": 2, "patterns": ["services", "application", "use_cases"]},
            {"name": "presentation", "level": 3, "patterns": ["views", "controllers", "api", "routes"]},
        ])

    def detect(self) -> List[Finding]:
        """Detect layered architecture violations."""
        findings: List[Finding] = []

        try:
            # Get import edges and layer assignments
            edges, file_layers, layer_defs, file_paths = self._extract_layer_data()

            if not edges:
                logger.debug("No import edges found for layer analysis")
                return findings

            # Run Rust detector
            violations = detect_layer_violations(edges, file_layers, layer_defs)

            # Group violations by type and layers
            grouped = self._group_violations(violations, layer_defs, file_paths)

            for (violation_type, src_layer, dst_layer), affected_files in grouped.items():
                finding = self._create_finding(
                    violation_type=violation_type,
                    src_layer_name=self._get_layer_name(src_layer, layer_defs),
                    dst_layer_name=self._get_layer_name(dst_layer, layer_defs),
                    affected_files=affected_files
                )
                findings.append(finding)

            logger.info(f"LayeredArchitectureDetector found {len(findings)} violations")
            return findings

        except Exception as e:
            logger.error(f"Error in LayeredArchitectureDetector: {e}", exc_info=True)
            return findings

    def _extract_layer_data(self):
        """Extract layer data from FalkorDB."""
        repo_filter = self._get_isolation_filter("f")

        # Get all files
        query = f"""
        MATCH (f:File)
        WHERE true {repo_filter}
        RETURN id(f) AS id, f.filePath AS path
        """

        results = self.db.execute_query(query, self._get_query_params())

        file_paths: Dict[int, str] = {}
        file_layers: Dict[int, int] = {}

        for record in results:
            file_id = record["id"]
            file_path = record["path"]
            file_paths[file_id] = file_path

            # Assign layer based on path patterns
            layer_id = self._classify_layer(file_path)
            if layer_id is not None:
                file_layers[file_id] = layer_id

        # Get import edges
        import_query = f"""
        MATCH (f1:File)-[:IMPORTS]->(f2:File)
        WHERE true {repo_filter}
        RETURN id(f1) AS source, id(f2) AS target
        """

        import_results = self.db.execute_query(import_query, self._get_query_params())

        edges = [(r["source"], r["target"]) for r in import_results]

        # Build layer definitions for Rust
        layer_defs = [
            (i, layer["name"], layer["level"])
            for i, layer in enumerate(self.layers)
        ]

        return edges, file_layers, layer_defs, file_paths

    def _classify_layer(self, file_path: str) -> Optional[int]:
        """Classify a file into a layer based on path patterns."""
        path_lower = file_path.lower()

        for layer_id, layer in enumerate(self.layers):
            for pattern in layer.get("patterns", []):
                if pattern in path_lower:
                    return layer_id

        return None  # Unclassified

    def _group_violations(self, violations, layer_defs, file_paths):
        """Group violations by type and layers."""
        layer_names = {ld[0]: ld[1] for ld in layer_defs}
        grouped: Dict[Tuple[str, int, int], List[str]] = {}

        for vtype, src_layer, dst_layer, src_file, dst_file in violations:
            key = (vtype, src_layer, dst_layer)
            if key not in grouped:
                grouped[key] = []

            src_path = file_paths.get(src_file, "unknown")
            grouped[key].append(src_path)

        return grouped

    def _get_layer_name(self, layer_id: int, layer_defs) -> str:
        """Get layer name from ID."""
        for ld in layer_defs:
            if ld[0] == layer_id:
                return ld[1]
        return f"layer_{layer_id}"

    def _create_finding(
        self,
        violation_type: str,
        src_layer_name: str,
        dst_layer_name: str,
        affected_files: List[str]
    ) -> Finding:
        """Create a finding for a layer violation."""
        finding_id = str(uuid.uuid4())

        severity = Severity.HIGH if violation_type == "back_call" else Severity.MEDIUM

        if violation_type == "back_call":
            title = f"Back-call: {src_layer_name} → {dst_layer_name}"
            description = (
                f"Lower layer '{src_layer_name}' is importing from higher layer '{dst_layer_name}'.\n"
                f"This violates dependency direction and creates tight coupling.\n"
                f"Affected files: {len(affected_files)}"
            )
        else:
            title = f"Skip-call: {src_layer_name} → {dst_layer_name}"
            description = (
                f"Layer '{src_layer_name}' is bypassing intermediate layers to reach '{dst_layer_name}'.\n"
                f"This breaks proper abstraction layers.\n"
                f"Affected files: {len(affected_files)}"
            )

        return Finding(
            id=finding_id,
            detector="LayeredArchitectureDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=affected_files[:10],  # Limit for readability
            affected_files=affected_files,
            graph_context={
                "violation_type": violation_type,
                "source_layer": src_layer_name,
                "target_layer": dst_layer_name,
                "violation_count": len(affected_files),
            },
            suggested_fix=self._suggest_fix(violation_type),
            estimated_effort=self._estimate_effort(len(affected_files)),
            created_at=datetime.now(),
        )

    def severity(self, finding: Finding) -> Severity:
        if finding.graph_context.get("violation_type") == "back_call":
            return Severity.HIGH
        return Severity.MEDIUM

    def _suggest_fix(self, violation_type: str) -> str:
        if violation_type == "back_call":
            return (
                "Fix back-call violations:\n"
                "1. Extract shared interfaces/types to a lower layer\n"
                "2. Use dependency injection to invert dependencies\n"
                "3. Consider if the import is truly needed"
            )
        return (
            "Fix skip-call violations:\n"
            "1. Route the dependency through the intermediate layer\n"
            "2. Create appropriate abstractions in intermediate layers\n"
            "3. Consider if the architecture needs restructuring"
        )

    def _estimate_effort(self, count: int) -> str:
        if count > 20:
            return "Large (1-2 weeks)"
        elif count > 5:
            return "Medium (2-4 days)"
        return "Small (1-2 days)"


class CallChainDepthDetector(CodeSmellDetector):
    """Detects deep call chains that indicate tight coupling.

    Deep call chains (>7-10 levels) can indicate:
    - Tight coupling across many modules
    - Potential performance issues
    - Difficult-to-understand code flow
    - Cascade failure risks
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.depth_threshold = self.config.get("depth_threshold", 7)
        self.max_depth = self.config.get("max_depth", 20)

    def detect(self) -> List[Finding]:
        """Detect deep call chains."""
        findings: List[Finding] = []

        try:
            # Get call edges
            call_edges, function_names = self._extract_call_graph()

            if not call_edges:
                logger.debug("No call edges found for chain analysis")
                return findings

            num_functions = len(function_names)

            # Run Rust detector
            chains = detect_deep_call_chains(call_edges, num_functions, self.max_depth)

            # Also find bottleneck functions
            bottlenecks = find_bottleneck_functions(
                call_edges, num_functions,
                min_chain_depth=self.depth_threshold,
                min_appearances=3,
                max_depth=self.max_depth
            )

            # Create findings for deep chains
            seen_starts: Set[int] = set()
            for start_func, depth, path in chains:
                if depth >= self.depth_threshold and start_func not in seen_starts:
                    seen_starts.add(start_func)
                    finding = self._create_chain_finding(
                        start_func=start_func,
                        depth=depth,
                        path=path,
                        function_names=function_names
                    )
                    findings.append(finding)

            # Create findings for bottlenecks
            for func_id, appearances in bottlenecks[:5]:  # Top 5 bottlenecks
                finding = self._create_bottleneck_finding(
                    func_id=func_id,
                    appearances=appearances,
                    function_names=function_names
                )
                findings.append(finding)

            logger.info(f"CallChainDepthDetector found {len(findings)} issues")
            return findings

        except Exception as e:
            logger.error(f"Error in CallChainDepthDetector: {e}", exc_info=True)
            return findings

    def _extract_call_graph(self) -> Tuple[List[Tuple[int, int]], Dict[int, str]]:
        """Extract call graph from FalkorDB."""
        repo_filter = self._get_isolation_filter("f")

        # Get all functions
        func_query = f"""
        MATCH (f:Function)
        WHERE true {repo_filter}
        RETURN id(f) AS id, f.qualifiedName AS name
        """

        results = self.db.execute_query(func_query, self._get_query_params())

        function_names: Dict[int, str] = {}
        id_to_idx: Dict[int, int] = {}

        for i, record in enumerate(results):
            func_id = record["id"]
            function_names[i] = record["name"]
            id_to_idx[func_id] = i

        # Get call edges
        call_query = f"""
        MATCH (f1:Function)-[:CALLS]->(f2:Function)
        WHERE true {repo_filter}
        RETURN id(f1) AS caller, id(f2) AS callee
        """

        call_results = self.db.execute_query(call_query, self._get_query_params())

        call_edges = []
        for record in call_results:
            caller_idx = id_to_idx.get(record["caller"])
            callee_idx = id_to_idx.get(record["callee"])
            if caller_idx is not None and callee_idx is not None:
                call_edges.append((caller_idx, callee_idx))

        return call_edges, function_names

    def _create_chain_finding(
        self,
        start_func: int,
        depth: int,
        path: List[int],
        function_names: Dict[int, str]
    ) -> Finding:
        """Create a finding for a deep call chain."""
        finding_id = str(uuid.uuid4())

        if depth > 15:
            severity = Severity.CRITICAL
        elif depth > 10:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        path_names = [function_names.get(i, f"func_{i}") for i in path[:10]]
        start_name = function_names.get(start_func, f"func_{start_func}")

        return Finding(
            id=finding_id,
            detector="CallChainDepthDetector",
            severity=severity,
            title=f"Deep call chain ({depth} levels) from {start_name.split('.')[-1]}",
            description=(
                f"Call chain starting at {start_name} reaches {depth} levels deep.\n"
                f"Path: {' → '.join(n.split('.')[-1] for n in path_names)}"
                f"{'...' if len(path) > 10 else ''}"
            ),
            affected_nodes=path_names,
            affected_files=[],
            graph_context={
                "depth": depth,
                "path": [function_names.get(i, f"func_{i}") for i in path],
                "start_function": start_name,
            },
            suggested_fix=self._suggest_fix(depth),
            estimated_effort=self._estimate_effort(depth),
            created_at=datetime.now(),
        )

    def _create_bottleneck_finding(
        self,
        func_id: int,
        appearances: int,
        function_names: Dict[int, str]
    ) -> Finding:
        """Create a finding for a bottleneck function."""
        finding_id = str(uuid.uuid4())

        func_name = function_names.get(func_id, f"func_{func_id}")

        return Finding(
            id=finding_id,
            detector="CallChainDepthDetector",
            severity=Severity.MEDIUM,
            title=f"Bottleneck function: {func_name.split('.')[-1]}",
            description=(
                f"Function {func_name} appears in {appearances} deep call chains.\n"
                f"This is a chokepoint where many call paths converge."
            ),
            affected_nodes=[func_name],
            affected_files=[],
            graph_context={
                "appearances": appearances,
                "function_name": func_name,
            },
            suggested_fix=(
                "Consider:\n"
                "1. Break this function into smaller, more focused units\n"
                "2. Use events/callbacks to decouple callers\n"
                "3. Evaluate if this concentration of calls is appropriate"
            ),
            estimated_effort="Medium (1-3 days)",
            created_at=datetime.now(),
        )

    def severity(self, finding: Finding) -> Severity:
        depth = finding.graph_context.get("depth", 0)
        if depth > 15:
            return Severity.CRITICAL
        elif depth > 10:
            return Severity.HIGH
        return Severity.MEDIUM

    def _suggest_fix(self, depth: int) -> str:
        return (
            f"Call chain depth of {depth} is too deep. Consider:\n"
            "1. Break long chains by introducing intermediate services\n"
            "2. Use async/event-driven patterns to decouple\n"
            "3. Apply the Law of Demeter (don't talk to strangers)\n"
            "4. Consider if some calls can be inlined or eliminated"
        )

    def _estimate_effort(self, depth: int) -> str:
        if depth > 15:
            return "Large (1-2 weeks)"
        elif depth > 10:
            return "Medium (2-4 days)"
        return "Small (1-2 days)"


class HubDependencyDetector(CodeSmellDetector):
    """Detects hub nodes in the dependency graph (architectural bottlenecks).

    Hubs are nodes with:
    - High betweenness centrality (many paths go through them)
    - High PageRank (many important nodes depend on them)

    These are single points of failure and change amplifiers.
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.percentile_threshold = self.config.get("percentile_threshold", 10.0)
        self.betweenness_weight = self.config.get("betweenness_weight", 0.6)
        self.pagerank_weight = self.config.get("pagerank_weight", 0.4)

    def detect(self) -> List[Finding]:
        """Detect hub dependencies."""
        findings: List[Finding] = []

        try:
            # Get dependency edges
            edges, node_names = self._extract_dependency_graph()

            if not edges:
                logger.debug("No dependency edges found for hub analysis")
                return findings

            num_nodes = len(node_names)

            # Run Rust detector
            hubs = detect_hub_dependencies(
                edges, num_nodes,
                self.betweenness_weight,
                self.pagerank_weight
            )

            for node_id, hub_score, betweenness, pagerank, in_degree, out_degree, percentile in hubs:
                if percentile <= self.percentile_threshold:
                    finding = self._create_finding(
                        node_name=node_names.get(node_id, f"node_{node_id}"),
                        hub_score=hub_score,
                        betweenness=betweenness,
                        pagerank=pagerank,
                        in_degree=in_degree,
                        out_degree=out_degree,
                        percentile=percentile
                    )
                    findings.append(finding)

            logger.info(f"HubDependencyDetector found {len(findings)} hubs")
            return findings

        except Exception as e:
            logger.error(f"Error in HubDependencyDetector: {e}", exc_info=True)
            return findings

    def _extract_dependency_graph(self) -> Tuple[List[Tuple[int, int]], Dict[int, str]]:
        """Extract dependency graph from FalkorDB."""
        repo_filter = self._get_isolation_filter("f")

        # Get all files/modules
        node_query = f"""
        MATCH (f:File)
        WHERE true {repo_filter}
        RETURN id(f) AS id, f.filePath AS name
        """

        results = self.db.execute_query(node_query, self._get_query_params())

        node_names: Dict[int, str] = {}
        id_to_idx: Dict[int, int] = {}

        for i, record in enumerate(results):
            node_id = record["id"]
            node_names[i] = record["name"]
            id_to_idx[node_id] = i

        # Get import edges
        edge_query = f"""
        MATCH (f1:File)-[:IMPORTS]->(f2:File)
        WHERE true {repo_filter}
        RETURN id(f1) AS source, id(f2) AS target
        """

        edge_results = self.db.execute_query(edge_query, self._get_query_params())

        edges = []
        for record in edge_results:
            src_idx = id_to_idx.get(record["source"])
            dst_idx = id_to_idx.get(record["target"])
            if src_idx is not None and dst_idx is not None:
                edges.append((src_idx, dst_idx))

        return edges, node_names

    def _create_finding(
        self,
        node_name: str,
        hub_score: float,
        betweenness: float,
        pagerank: float,
        in_degree: int,
        out_degree: int,
        percentile: float
    ) -> Finding:
        """Create a finding for a hub node."""
        finding_id = str(uuid.uuid4())

        if percentile <= 1.0:
            severity = Severity.CRITICAL
        elif percentile <= 5.0:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        short_name = node_name.split("/")[-1]

        return Finding(
            id=finding_id,
            detector="HubDependencyDetector",
            severity=severity,
            title=f"Architectural hub: {short_name}",
            description=(
                f"File {node_name} is an architectural hub (top {percentile:.1f}%).\n"
                f"In-degree: {in_degree}, Out-degree: {out_degree}\n"
                f"Betweenness: {betweenness:.4f}, PageRank: {pagerank:.4f}\n"
                "Many code paths flow through this file."
            ),
            affected_nodes=[node_name],
            affected_files=[node_name],
            graph_context={
                "hub_score": hub_score,
                "betweenness": betweenness,
                "pagerank": pagerank,
                "in_degree": in_degree,
                "out_degree": out_degree,
                "percentile": percentile,
            },
            suggested_fix=self._suggest_fix(in_degree, out_degree),
            estimated_effort=self._estimate_effort(percentile),
            created_at=datetime.now(),
        )

    def severity(self, finding: Finding) -> Severity:
        percentile = finding.graph_context.get("percentile", 50)
        if percentile <= 1.0:
            return Severity.CRITICAL
        elif percentile <= 5.0:
            return Severity.HIGH
        return Severity.MEDIUM

    def _suggest_fix(self, in_degree: int, out_degree: int) -> str:
        suggestions = ["This is an architectural bottleneck. Consider:"]

        if in_degree > out_degree:
            suggestions.append("1. Extract interfaces to reduce direct dependencies")
            suggestions.append("2. Use dependency injection to invert control")
        else:
            suggestions.append("1. Apply the Single Responsibility Principle")
            suggestions.append("2. Split into smaller, focused modules")

        suggestions.append("3. Ensure comprehensive test coverage for this critical code")
        suggestions.append("4. Document why this hub exists if intentional")

        return "\n".join(suggestions)

    def _estimate_effort(self, percentile: float) -> str:
        if percentile <= 1.0:
            return "Large (2-4 weeks)"
        elif percentile <= 5.0:
            return "Medium (1-2 weeks)"
        return "Small (2-4 days)"


class ChangeCouplingDetector(CodeSmellDetector):
    """Detects change coupling (files that frequently change together).

    Based on git commit history analysis. Files that change together
    without explicit dependencies may indicate:
    - Hidden logical coupling
    - Copy-paste code
    - Missing abstractions
    - Shotgun surgery smell
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.min_support = self.config.get("min_support", 0.05)
        self.min_confidence = self.config.get("min_confidence", 0.5)

    def detect(self) -> List[Finding]:
        """Detect change coupling between files."""
        findings: List[Finding] = []

        try:
            # Get commit history from graph (if available)
            commit_files, explicit_deps, file_names = self._extract_commit_data()

            if not commit_files:
                logger.debug("No commit history found for coupling analysis")
                return findings

            # Run Rust detector
            couplings = detect_change_coupling(
                commit_files, explicit_deps,
                self.min_support, self.min_confidence
            )

            for file_a, file_b, co_changes, support, conf_a_b, conf_b_a in couplings:
                finding = self._create_finding(
                    file_a_name=file_names.get(file_a, f"file_{file_a}"),
                    file_b_name=file_names.get(file_b, f"file_{file_b}"),
                    co_changes=co_changes,
                    support=support,
                    confidence=max(conf_a_b, conf_b_a)
                )
                findings.append(finding)

            logger.info(f"ChangeCouplingDetector found {len(findings)} couplings")
            return findings

        except Exception as e:
            logger.error(f"Error in ChangeCouplingDetector: {e}", exc_info=True)
            return findings

    def _extract_commit_data(self) -> Tuple[List[List[int]], List[Tuple[int, int]], Dict[int, str]]:
        """Extract commit data from FalkorDB.

        This requires the graph to have Commit nodes with MODIFIES relationships.
        """
        repo_filter = self._get_isolation_filter("f")

        # Get file name mapping
        file_query = f"""
        MATCH (f:File)
        WHERE true {repo_filter}
        RETURN id(f) AS id, f.filePath AS name
        """

        file_results = self.db.execute_query(file_query, self._get_query_params())

        file_names: Dict[int, str] = {}
        id_to_idx: Dict[int, int] = {}

        for i, record in enumerate(file_results):
            file_id = record["id"]
            file_names[i] = record["name"]
            id_to_idx[file_id] = i

        # Get commit history (if available)
        commit_query = f"""
        MATCH (c:Commit)-[:MODIFIES]->(f:File)
        WHERE true {repo_filter}
        WITH c, collect(id(f)) AS files
        WHERE size(files) > 1 AND size(files) < 50
        RETURN files
        LIMIT 1000
        """

        try:
            commit_results = self.db.execute_query(commit_query, self._get_query_params())
        except Exception:
            # Commit nodes may not exist
            return [], [], file_names

        commit_files: List[List[int]] = []
        for record in commit_results:
            files = record.get("files", [])
            indexed_files = [id_to_idx[f] for f in files if f in id_to_idx]
            if len(indexed_files) > 1:
                commit_files.append(indexed_files)

        # Get explicit dependencies
        dep_query = f"""
        MATCH (f1:File)-[:IMPORTS]->(f2:File)
        WHERE true {repo_filter}
        RETURN id(f1) AS source, id(f2) AS target
        """

        dep_results = self.db.execute_query(dep_query, self._get_query_params())

        explicit_deps = []
        for record in dep_results:
            src = id_to_idx.get(record["source"])
            dst = id_to_idx.get(record["target"])
            if src is not None and dst is not None:
                explicit_deps.append((src, dst))

        return commit_files, explicit_deps, file_names

    def _create_finding(
        self,
        file_a_name: str,
        file_b_name: str,
        co_changes: int,
        support: float,
        confidence: float
    ) -> Finding:
        """Create a finding for change coupling."""
        finding_id = str(uuid.uuid4())

        if confidence > 0.8:
            severity = Severity.HIGH
        elif confidence > 0.5:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        short_a = file_a_name.split("/")[-1]
        short_b = file_b_name.split("/")[-1]

        return Finding(
            id=finding_id,
            detector="ChangeCouplingDetector",
            severity=severity,
            title=f"Hidden coupling: {short_a} ↔ {short_b}",
            description=(
                f"Files frequently change together ({co_changes} co-changes, "
                f"{confidence:.0%} confidence) but have no explicit dependency.\n"
                f"This may indicate hidden logical coupling."
            ),
            affected_nodes=[file_a_name, file_b_name],
            affected_files=[file_a_name, file_b_name],
            graph_context={
                "co_changes": co_changes,
                "support": support,
                "confidence": confidence,
            },
            suggested_fix=self._suggest_fix(),
            estimated_effort="Medium (1-3 days)",
            created_at=datetime.now(),
        )

    def severity(self, finding: Finding) -> Severity:
        confidence = finding.graph_context.get("confidence", 0)
        if confidence > 0.8:
            return Severity.HIGH
        elif confidence > 0.5:
            return Severity.MEDIUM
        return Severity.LOW

    def _suggest_fix(self) -> str:
        return (
            "Files with hidden coupling should be investigated:\n"
            "1. Check for copy-paste code and extract shared logic\n"
            "2. Consider if an explicit dependency should be added\n"
            "3. Look for missing abstractions that should be extracted\n"
            "4. Evaluate if these files should be merged"
        )
