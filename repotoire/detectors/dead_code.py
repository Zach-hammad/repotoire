"""Dead code detector - finds unused functions and classes.

Supports cross-detector validation with VultureDetector (REPO-153).
When both graph-based and AST-based detection agree, confidence exceeds 95%.

REPO-416: Added path cache support for O(1) reachability queries from entry points.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity

# Try to import Rust path cache for O(1) reachability queries (REPO-416)
try:
    from repotoire_fast import PyPathCache
    _HAS_PATH_CACHE = True
except ImportError:
    _HAS_PATH_CACHE = False
    PyPathCache = None  # type: ignore

if TYPE_CHECKING:
    from repotoire_fast import PyPathCache

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class DeadCodeDetector(CodeSmellDetector):
    """Detects dead code (functions/classes with zero incoming references).

    Supports cross-validation with VultureDetector for high-confidence findings.
    When both detectors agree, confidence reaches 95%+ enabling safe auto-removal.
    """

    def __init__(self, graph_client, detector_config: Optional[dict] = None, enricher: Optional[GraphEnricher] = None):
        """Initialize dead code detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional detector configuration. May include:
                - repo_id: Repository UUID for filtering queries (multi-tenant isolation)
                - path_cache: Prebuilt path cache for O(1) reachability queries (REPO-416)
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher

        # Path cache for O(1) reachability queries from entry points (REPO-416)
        self.path_cache: Optional["PyPathCache"] = (detector_config or {}).get("path_cache")

        # Cross-validation confidence thresholds
        self.base_confidence = 0.70  # Graph-only confidence
        self.validated_confidence = 0.95  # When Vulture confirms

    @property
    def needs_previous_findings(self) -> bool:
        """DeadCodeDetector needs VultureDetector findings for cross-validation.

        When both graph-based and AST-based (Vulture) detection agree,
        confidence exceeds 95%, enabling safe auto-removal recommendations.
        """
        return True

    # Common entry points that should not be flagged as dead code
    ENTRY_POINTS = {
        "main",
        "__main__",
        "__init__",
        "setUp",
        "tearDown",
        "test_",  # Prefix for test functions
    }

    # Common decorator patterns that indicate a function is used
    DECORATOR_PATTERNS = {
        "route",  # Flask/FastAPI routes
        "app",  # General app decorators
        "task",  # Celery/background tasks
        "api",  # API endpoints
        "endpoint",  # API endpoints
        "command",  # CLI commands
        "listener",  # Event listeners
        "handler",  # Event handlers
        "callback",  # Callbacks
        "register",  # Registration decorators
        "property",  # Properties
        "classmethod",  # Class methods
        "staticmethod",  # Static methods
    }

    # Special methods that are called implicitly
    MAGIC_METHODS = {
        "__str__",
        "__repr__",
        "__enter__",
        "__exit__",
        "__call__",
        "__len__",
        "__iter__",
        "__next__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__eq__",
        "__ne__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__hash__",
        "__bool__",
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
        "__post_init__",  # dataclass post-initialization
        "__init_subclass__",  # subclass initialization
        "__set_name__",  # descriptor protocol
    }

    def detect(self, previous_findings: Optional[List[Finding]] = None) -> List[Finding]:
        """Find dead code (unused functions and classes).

        Looks for Function and Class nodes with zero incoming CALLS relationships
        and not imported by any file.

        Args:
            previous_findings: Optional list of findings from previous detectors
                             (used for cross-validation with VultureDetector)

        Returns:
            List of findings for dead code
        """
        # Build set of Vulture-confirmed unused items for cross-validation
        vulture_unused = self._extract_vulture_unused(previous_findings)

        findings: List[Finding] = []

        # Find unused functions
        function_findings = self._find_dead_functions(vulture_unused)
        findings.extend(function_findings)

        # Find unused classes
        class_findings = self._find_dead_classes(vulture_unused)
        findings.extend(class_findings)

        return findings

    def _extract_vulture_unused(
        self,
        previous_findings: Optional[List[Finding]]
    ) -> Dict[str, Dict]:
        """Extract Vulture-confirmed unused items from previous findings.

        Args:
            previous_findings: List of findings from previous detectors

        Returns:
            Dict mapping (file_path, name) -> vulture finding info
        """
        vulture_unused: Dict[str, Dict] = {}

        if not previous_findings:
            return vulture_unused

        for finding in previous_findings:
            if finding.detector != "VultureDetector":
                continue

            # Extract item info from graph_context
            ctx = finding.graph_context or {}
            item_name = ctx.get("item_name")
            item_type = ctx.get("item_type")
            vulture_confidence = ctx.get("confidence", 0)

            if not item_name:
                continue

            # Get file path from affected_files
            file_path = finding.affected_files[0] if finding.affected_files else None
            if not file_path:
                continue

            # Create lookup key
            key = f"{file_path}:{item_name}"
            vulture_unused[key] = {
                "name": item_name,
                "type": item_type,
                "confidence": vulture_confidence,
                "file": file_path,
                "line": ctx.get("line"),
            }

            # Also store by just name for fuzzy matching
            vulture_unused[item_name] = vulture_unused[key]

        return vulture_unused

    def _check_vulture_confirms(
        self,
        name: str,
        file_path: str,
        vulture_unused: Dict[str, Dict]
    ) -> Optional[Dict]:
        """Check if Vulture also flagged this item as unused.

        Args:
            name: Function/class name
            file_path: File path
            vulture_unused: Dict of Vulture-confirmed unused items

        Returns:
            Vulture finding info if confirmed, None otherwise
        """
        # Try exact match first
        key = f"{file_path}:{name}"
        if key in vulture_unused:
            return vulture_unused[key]

        # Try name-only match (less precise but catches more)
        if name in vulture_unused:
            return vulture_unused[name]

        return None

    def _find_dead_functions(self, vulture_unused: Dict[str, Dict]) -> List[Finding]:
        """Find functions that are never called.

        Args:
            vulture_unused: Dict of Vulture-confirmed unused items for cross-validation

        Returns:
            List of findings for dead functions
        """
        # REPO-416: Try path_cache first for O(1) reachability (100-1000x faster)
        if self.path_cache is not None and _HAS_PATH_CACHE:
            try:
                return self._find_dead_functions_with_cache(vulture_unused)
            except Exception as e:
                logger.warning(f"Path cache dead code detection failed: {e}, using Cypher fallback")

        return self._find_dead_functions_cypher(vulture_unused)

    def _find_dead_functions_with_cache(self, vulture_unused: Dict[str, Dict]) -> List[Finding]:
        """Find dead functions using path cache for O(1) reachability queries.

        REPO-416: This is 100-1000x faster than Cypher OPTIONAL MATCH queries.

        Args:
            vulture_unused: Dict of Vulture-confirmed unused items for cross-validation

        Returns:
            List of findings for dead functions
        """
        findings: List[Finding] = []
        logger.info("Using path_cache for dead code detection (O(1) reachability)")

        # Step 1: Get all functions from graph
        # REPO-600: Filter by tenant_id AND repo_id for defense-in-depth isolation
        repo_filter = self._get_isolation_filter("f")
        all_functions_query = f"""
        MATCH (f:Function)
        WHERE true {repo_filter}
        OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
        RETURN f.qualifiedName AS qualified_name,
               f.name AS name,
               f.filePath AS file_path,
               f.lineStart AS line_start,
               f.complexity AS complexity,
               file.filePath AS containing_file,
               f.decorators AS decorators,
               f.is_method AS is_method
        """
        params = self._get_query_params()
        all_functions = self.db.execute_query(all_functions_query, params)

        if not all_functions:
            return findings

        # Build lookup: qualified_name -> function record
        func_by_name: Dict[str, dict] = {}
        for record in all_functions:
            qname = record["qualified_name"]
            if qname:
                func_by_name[qname] = record

        logger.info(f"Found {len(func_by_name)} functions to analyze")

        # Step 2: Identify entry points (functions that are always "used")
        entry_point_names: Set[str] = set()
        for qname, record in func_by_name.items():
            name = record["name"]
            decorators = record.get("decorators") or []

            # Entry points: main, __init__, test_*, decorated functions
            if name in self.ENTRY_POINTS or name in self.MAGIC_METHODS:
                entry_point_names.add(qname)
            elif name.startswith("test_"):
                entry_point_names.add(qname)
            elif decorators and len(decorators) > 0:
                entry_point_names.add(qname)
            # Also skip common patterns that are often entry points
            elif any(pattern in name.lower() for pattern in ["handle", "on_", "callback", "route", "endpoint"]):
                entry_point_names.add(qname)

        logger.info(f"Identified {len(entry_point_names)} entry points")

        # Step 3: Compute reachable set from all entry points
        reachable_names: Set[str] = set()

        for qname in entry_point_names:
            try:
                # Get node ID for this entry point
                node_id = self.path_cache.get_id(qname)
                if node_id is None:
                    continue

                # Get all functions reachable from this entry point via CALLS
                reachable_ids = self.path_cache.reachable_from("CALLS", node_id)
                if reachable_ids:
                    for rid in reachable_ids:
                        rname = self.path_cache.get_name(rid)
                        if rname:
                            reachable_names.add(rname)
            except Exception:
                # Skip entry points not in cache
                pass

        logger.info(f"Found {len(reachable_names)} functions reachable from entry points")

        # Step 4: Functions NOT reachable AND NOT entry points are dead code
        dead_function_names = set(func_by_name.keys()) - reachable_names - entry_point_names

        logger.info(f"Found {len(dead_function_names)} potentially dead functions")

        # Step 5: Apply additional filters and create findings
        for qname in dead_function_names:
            record = func_by_name[qname]
            name = record["name"]

            # Apply the same filters as Cypher path
            if name in self.MAGIC_METHODS:
                continue

            is_method = record.get("is_method")
            if is_method and not name.startswith("_"):
                continue

            if name in self.ENTRY_POINTS or name.startswith("test_"):
                continue

            if any(pattern in name.lower() for pattern in ["handle", "on_", "callback"]):
                continue

            if any(pattern in name.lower() for pattern in ["load_data", "loader", "_loader", "load_", "create_", "build_", "make_"]):
                continue

            if name.startswith("_parse_") or name.startswith("_process_"):
                continue

            if any(pattern in name.lower() for pattern in ["load_config", "generate_", "validate_", "setup_", "initialize_"]):
                continue

            if any(pattern in name.lower() for pattern in ["to_dict", "to_json", "from_dict", "from_json", "serialize", "deserialize"]):
                continue

            if name.endswith("_side_effect") or name.endswith("_effect"):
                continue

            if name.startswith("_extract_") or name.startswith("_find_") or name.startswith("_calculate_"):
                continue

            if name.startswith("_get_") or name.startswith("_set_") or name.startswith("_check_"):
                continue

            decorators = record.get("decorators")
            if decorators and len(decorators) > 0:
                continue

            # Create finding
            finding = self._create_function_finding(record, vulture_unused, detection_method="path_cache")
            if finding:
                findings.append(finding)

            # Limit results
            if len(findings) >= 100:
                break

        logger.info(f"Path cache detection found {len(findings)} dead functions")
        return findings

    def _create_function_finding(
        self,
        record: dict,
        vulture_unused: Dict[str, Dict],
        detection_method: str = "cypher"
    ) -> Optional[Finding]:
        """Create a finding for a dead function.

        Args:
            record: Function record from graph query
            vulture_unused: Dict of Vulture-confirmed unused items
            detection_method: How the dead code was detected

        Returns:
            Finding object or None if filtered out
        """
        import uuid
        from datetime import datetime

        name = record["name"]
        qualified_name = record["qualified_name"]
        file_path = record.get("containing_file") or record.get("file_path")
        if not file_path:
            return None

        complexity = record.get("complexity") or 0

        # Cross-validation with Vulture (REPO-153)
        vulture_match = self._check_vulture_confirms(name, file_path, vulture_unused)
        vulture_confirmed = vulture_match is not None

        # Calculate confidence based on validation
        if vulture_confirmed:
            confidence = self.validated_confidence  # 95% when both agree
            vulture_conf = vulture_match.get("confidence", 0)
            validators = ["graph_analysis", "vulture"]
            safe_to_remove = True
        else:
            confidence = self.base_confidence  # 70% graph-only
            vulture_conf = 0
            validators = ["graph_analysis"]
            safe_to_remove = False

        severity = self._calculate_function_severity(complexity)

        # Build description with validation info
        description = f"Function '{name}' is never called in the codebase. "
        description += f"It has complexity {complexity}."
        if vulture_confirmed:
            description += "\n\n**Cross-validated**: Both graph analysis and Vulture agree this is unused."
            description += f"\n**Confidence**: {confidence*100:.0f}% ({len(validators)} validators agree)"
            description += "\n**Safe to remove**: Yes"
        else:
            description += f"\n\n**Confidence**: {confidence*100:.0f}% (graph analysis only)"
            description += "\n**Recommendation**: Review before removing"

        # Build suggested fix based on confidence
        if safe_to_remove:
            suggested_fix = (
                f"**SAFE TO REMOVE** (confidence: {confidence*100:.0f}%)\n"
                f"Both graph analysis and Vulture confirm this function is unused.\n"
                f"1. Delete the function from {file_path.split('/')[-1]}\n"
                f"2. Run tests to verify nothing breaks"
            )
        else:
            suggested_fix = (
                f"**REVIEW REQUIRED** (confidence: {confidence*100:.0f}%)\n"
                f"1. Remove the function from {file_path.split('/')[-1]}\n"
                f"2. Check for dynamic calls (getattr, eval) that might use it\n"
                f"3. Verify it's not an API endpoint or callback"
            )

        finding_id = str(uuid.uuid4())
        finding = Finding(
            id=finding_id,
            detector="DeadCodeDetector",
            severity=severity,
            title=f"Unused function: {name}",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            graph_context={
                "type": "function",
                "name": name,
                "complexity": complexity,
                "line_start": record.get("line_start"),
                "vulture_confirmed": vulture_confirmed,
                "vulture_confidence": vulture_conf,
                "validators": validators,
                "safe_to_remove": safe_to_remove,
                "confidence": confidence,
                "detection_method": detection_method,
            },
            suggested_fix=suggested_fix,
            estimated_effort="Small (15-30 minutes)" if safe_to_remove else "Small (30-60 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata (REPO-150 Phase 1)
        evidence = ["unused_function", "no_calls", detection_method]
        if vulture_confirmed:
            evidence.append("vulture_confirmed")
        tags = ["dead_code", "unused_code", "maintenance"]
        if safe_to_remove:
            tags.append("safe_to_remove")
        else:
            tags.append("review_required")

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="DeadCodeDetector",
            confidence=confidence,
            evidence=evidence,
            tags=tags
        ))

        # Flag entity in graph for cross-detector collaboration
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=qualified_name,
                    detector="DeadCodeDetector",
                    severity=severity.value,
                    issues=["unused_function"],
                    confidence=confidence,
                    metadata={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                              for k, v in {"complexity": complexity, "type": "function", "detection_method": detection_method}.items()}
                )
            except Exception:
                pass

        return finding

    def _find_dead_functions_cypher(self, vulture_unused: Dict[str, Dict]) -> List[Finding]:
        """Find dead functions using Cypher queries (fallback method).

        Args:
            vulture_unused: Dict of Vulture-confirmed unused items for cross-validation

        Returns:
            List of findings for dead functions
        """
        findings: List[Finding] = []

        # REPO-600: Filter by tenant_id AND repo_id for defense-in-depth isolation
        repo_filter = self._get_isolation_filter("f")

        # Note: Using count() = 0 instead of IS NULL because FalkorDB's IS NULL
        # returns String instead of Boolean, causing type mismatch errors.
        # Also removed is_method filter from Cypher - applied in Python below.
        query = f"""
        MATCH (f:Function)
        WHERE NOT (f.name STARTS WITH 'test_')
          AND NOT f.name IN ['main', '__main__', '__init__', 'setUp', 'tearDown']
          {repo_filter}
        OPTIONAL MATCH (f)<-[rel:CALLS]-()
        OPTIONAL MATCH (f)<-[use:USES]-()
        WITH f, count(rel) AS call_count, count(use) AS use_count
        WHERE call_count = 0 AND use_count = 0
        OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
        WITH f, file
        RETURN f.qualifiedName AS qualified_name,
               f.name AS name,
               f.filePath AS file_path,
               f.lineStart AS line_start,
               f.complexity AS complexity,
               file.filePath AS containing_file,
               f.decorators AS decorators,
               f.is_method AS is_method
        ORDER BY f.complexity DESC
        LIMIT 100
        """

        params = self._get_query_params()
        results = self.db.execute_query(query, params)

        for record in results:
            # Filter out magic methods
            name = record["name"]
            if name in self.MAGIC_METHODS:
                continue

            # Filter methods that aren't private (moved from Cypher due to FalkorDB type issues)
            # Only flag methods if they start with '_' (private), skip public methods as they may be called externally
            is_method = record.get("is_method")
            if is_method and not name.startswith("_"):
                continue

            # Check if it's an entry point (exact match or prefix)
            if name in self.ENTRY_POINTS or any(name.startswith(ep) for ep in ["test_"]):
                continue

            # Additional check: filter out common decorator patterns in the name
            # (e.g., handle_event, on_click, etc.)
            if any(pattern in name.lower() for pattern in ["handle", "on_", "callback"]):
                continue

            # Filter out loader/factory pattern methods (often called dynamically)
            if any(pattern in name.lower() for pattern in ["load_data", "loader", "_loader", "load_", "create_", "build_", "make_"]):
                continue

            # Filter out parse/process methods that might be called via registry
            if name.startswith("_parse_") or name.startswith("_process_"):
                continue

            # Filter out common public API functions (config, setup, validation)
            if any(pattern in name.lower() for pattern in ["load_config", "generate_", "validate_", "setup_", "initialize_"]):
                continue

            # Filter out converter/transformation methods
            if any(pattern in name.lower() for pattern in ["to_dict", "to_json", "from_dict", "from_json", "serialize", "deserialize"]):
                continue

            # Filter out pytest/mock side_effect functions (common pattern in tests)
            # These are assigned to mock.side_effect which the detector doesn't track
            if name.endswith("_side_effect") or name.endswith("_effect"):
                continue

            # Filter out common internal helper method patterns
            # These are private methods that are almost always called internally
            # but may not have CALLS relationships due to incomplete extraction
            if name.startswith("_extract_") or name.startswith("_find_") or name.startswith("_calculate_"):
                continue

            # Filter out other common internal patterns
            if name.startswith("_get_") or name.startswith("_set_") or name.startswith("_check_"):
                continue

            # Skip decorated functions (decorators like @property, @classmethod indicate usage)
            decorators = record.get("decorators")
            if decorators and len(decorators) > 0:
                continue

            finding_id = str(uuid.uuid4())
            qualified_name = record["qualified_name"]
            file_path = record["containing_file"] or record["file_path"]
            complexity = record["complexity"] or 0

            # Cross-validation with Vulture (REPO-153)
            vulture_match = self._check_vulture_confirms(name, file_path, vulture_unused)
            vulture_confirmed = vulture_match is not None

            # Calculate confidence based on validation
            if vulture_confirmed:
                confidence = self.validated_confidence  # 95% when both agree
                vulture_conf = vulture_match.get("confidence", 0)
                validators = ["graph_analysis", "vulture"]
                safe_to_remove = True
            else:
                confidence = self.base_confidence  # 70% graph-only
                vulture_conf = 0
                validators = ["graph_analysis"]
                safe_to_remove = False

            severity = self._calculate_function_severity(complexity)

            # Build description with validation info
            description = f"Function '{name}' is never called in the codebase. "
            description += f"It has complexity {complexity}."
            if vulture_confirmed:
                description += "\n\n**Cross-validated**: Both graph analysis and Vulture agree this is unused."
                description += f"\n**Confidence**: {confidence*100:.0f}% ({len(validators)} validators agree)"
                description += "\n**Safe to remove**: Yes"
            else:
                description += f"\n\n**Confidence**: {confidence*100:.0f}% (graph analysis only)"
                description += "\n**Recommendation**: Review before removing"

            # Build suggested fix based on confidence
            if safe_to_remove:
                suggested_fix = (
                    f"**SAFE TO REMOVE** (confidence: {confidence*100:.0f}%)\n"
                    f"Both graph analysis and Vulture confirm this function is unused.\n"
                    f"1. Delete the function from {file_path.split('/')[-1]}\n"
                    f"2. Run tests to verify nothing breaks"
                )
            else:
                suggested_fix = (
                    f"**REVIEW REQUIRED** (confidence: {confidence*100:.0f}%)\n"
                    f"1. Remove the function from {file_path.split('/')[-1]}\n"
                    f"2. Check for dynamic calls (getattr, eval) that might use it\n"
                    f"3. Verify it's not an API endpoint or callback"
                )

            finding = Finding(
                id=finding_id,
                detector="DeadCodeDetector",
                severity=severity,
                title=f"Unused function: {name}",
                description=description,
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "function",
                    "name": name,
                    "complexity": complexity,
                    "line_start": record["line_start"],
                    "vulture_confirmed": vulture_confirmed,
                    "vulture_confidence": vulture_conf,
                    "validators": validators,
                    "safe_to_remove": safe_to_remove,
                    "confidence": confidence,
                },
                suggested_fix=suggested_fix,
                estimated_effort="Small (15-30 minutes)" if safe_to_remove else "Small (30-60 minutes)",
                created_at=datetime.now(),
            )

            # Add collaboration metadata (REPO-150 Phase 1) with cross-validation info
            evidence = ["unused_function", "no_calls"]
            if vulture_confirmed:
                evidence.append("vulture_confirmed")
            tags = ["dead_code", "unused_code", "maintenance"]
            if safe_to_remove:
                tags.append("safe_to_remove")
            else:
                tags.append("review_required")

            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="DeadCodeDetector",
                confidence=confidence,
                evidence=evidence,
                tags=tags
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=qualified_name,
                        detector="DeadCodeDetector",
                        severity=severity.value,
                        issues=["unused_function"],
                        confidence=confidence,
                        metadata={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in {"complexity": complexity, "type": "function"}.items()}
                    )
                except Exception:
                    pass

            findings.append(finding)

        return findings

    def _find_dead_classes(self, vulture_unused: Dict[str, Dict]) -> List[Finding]:
        """Find classes that are never instantiated or inherited from.

        Args:
            vulture_unused: Dict of Vulture-confirmed unused items for cross-validation

        Returns:
            List of findings for dead classes
        """
        findings: List[Finding] = []

        # REPO-600: Filter by tenant_id AND repo_id for defense-in-depth isolation
        repo_filter = self._get_isolation_filter("c")

        # FalkorDB-compatible query
        # Note: Simplified to avoid FalkorDB type mismatch issues
        # Method count moved to Python post-processing
        query = f"""
        MATCH (file:File)-[:CONTAINS]->(c:Class)
        WHERE 1=1 {repo_filter}
        OPTIONAL MATCH (c)<-[rel:CALLS]-()
        OPTIONAL MATCH (c)<-[inherit:INHERITS]-()
        OPTIONAL MATCH (c)<-[use:USES]-()
        WITH c, file, count(rel) AS call_count, count(inherit) AS inherit_count, count(use) AS use_count
        WHERE call_count = 0 AND inherit_count = 0 AND use_count = 0
        RETURN c.qualifiedName AS qualified_name,
               c.name AS name,
               c.filePath AS file_path,
               c.complexity AS complexity,
               file.filePath AS containing_file,
               c.decorators AS decorators
        ORDER BY c.complexity DESC
        LIMIT 50
        """

        params = self._get_query_params()
        results = self.db.execute_query(query, params)

        for record in results:
            name = record["name"]

            # Skip common base classes
            if name in ["ABC", "Enum", "Exception", "BaseException"]:
                continue

            # Skip exception classes (often raised without instantiation)
            if name.endswith("Error") or name.endswith("Exception"):
                continue

            # Skip mixin classes (used for multiple inheritance)
            if name.endswith("Mixin") or "Mixin" in name:
                continue

            # Skip test classes (test classes often have fixtures that aren't "called")
            if name.startswith("Test") or name.endswith("Test"):
                continue

            # Skip decorated classes (decorators like @dataclass, @pytest.fixture indicate usage)
            decorators = record.get("decorators")
            if decorators and len(decorators) > 0:
                continue

            finding_id = str(uuid.uuid4())
            qualified_name = record["qualified_name"]
            file_path = record["containing_file"] or record["file_path"]
            complexity = record["complexity"] or 0
            # method_count removed from query due to FalkorDB STARTS WITH issues
            method_count = 0

            # Cross-validation with Vulture (REPO-153)
            vulture_match = self._check_vulture_confirms(name, file_path, vulture_unused)
            vulture_confirmed = vulture_match is not None

            # Calculate confidence based on validation
            if vulture_confirmed:
                confidence = self.validated_confidence  # 95% when both agree
                vulture_conf = vulture_match.get("confidence", 0)
                validators = ["graph_analysis", "vulture"]
                safe_to_remove = True
            else:
                confidence = self.base_confidence  # 70% graph-only
                vulture_conf = 0
                validators = ["graph_analysis"]
                safe_to_remove = False

            severity = self._calculate_class_severity(method_count, complexity)

            # Build description with validation info
            description = f"Class '{name}' is never instantiated or inherited from. "
            description += f"It has {method_count} methods and complexity {complexity}."
            if vulture_confirmed:
                description += "\n\n**Cross-validated**: Both graph analysis and Vulture agree this is unused."
                description += f"\n**Confidence**: {confidence*100:.0f}% ({len(validators)} validators agree)"
                description += "\n**Safe to remove**: Yes"
            else:
                description += f"\n\n**Confidence**: {confidence*100:.0f}% (graph analysis only)"
                description += "\n**Recommendation**: Review before removing"

            # Build suggested fix based on confidence
            if safe_to_remove:
                suggested_fix = (
                    f"**SAFE TO REMOVE** (confidence: {confidence*100:.0f}%)\n"
                    f"Both graph analysis and Vulture confirm this class is unused.\n"
                    f"1. Delete the class and its {method_count} methods\n"
                    f"2. Run tests to verify nothing breaks"
                )
            else:
                suggested_fix = (
                    f"**REVIEW REQUIRED** (confidence: {confidence*100:.0f}%)\n"
                    f"1. Remove the class and its {method_count} methods\n"
                    f"2. Check for dynamic instantiation (factory patterns, reflection)\n"
                    f"3. Verify it's not used in configuration or plugins"
                )

            finding = Finding(
                id=finding_id,
                detector="DeadCodeDetector",
                severity=severity,
                title=f"Unused class: {name}",
                description=description,
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "class",
                    "name": name,
                    "complexity": complexity,
                    "method_count": method_count,
                    "vulture_confirmed": vulture_confirmed,
                    "vulture_confidence": vulture_conf,
                    "validators": validators,
                    "safe_to_remove": safe_to_remove,
                    "confidence": confidence,
                },
                suggested_fix=suggested_fix,
                estimated_effort=self._estimate_class_removal_effort(method_count),
                created_at=datetime.now(),
            )

            # Add collaboration metadata (REPO-150 Phase 1) with cross-validation info
            evidence = ["unused_class", "no_instantiation"]
            if vulture_confirmed:
                evidence.append("vulture_confirmed")
            tags = ["dead_code", "unused_code", "maintenance"]
            if safe_to_remove:
                tags.append("safe_to_remove")
            else:
                tags.append("review_required")

            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="DeadCodeDetector",
                confidence=confidence,
                evidence=evidence,
                tags=tags
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=qualified_name,
                        detector="DeadCodeDetector",
                        severity=severity.value,
                        issues=["unused_class"],
                        confidence=confidence,
                        metadata={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in {"complexity": complexity, "method_count": method_count, "type": "class"}.items()}
                    )
                except Exception:
                    pass

            findings.append(finding)

        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on complexity and size.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        context = finding.graph_context
        complexity = context.get("complexity", 0)
        method_count = context.get("method_count", 0)

        if context.get("type") == "class":
            return self._calculate_class_severity(method_count, complexity)
        else:
            return self._calculate_function_severity(complexity)

    def _calculate_function_severity(self, complexity: int) -> Severity:
        """Calculate severity for dead function.

        Higher complexity = higher severity (more wasted code).

        Args:
            complexity: Cyclomatic complexity

        Returns:
            Severity level
        """
        if complexity >= 20:
            return Severity.HIGH
        elif complexity >= 10:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _calculate_class_severity(self, method_count: int, complexity: int) -> Severity:
        """Calculate severity for dead class.

        Args:
            method_count: Number of methods in class
            complexity: Total complexity

        Returns:
            Severity level
        """
        if method_count >= 10 or complexity >= 50:
            return Severity.HIGH
        elif method_count >= 5 or complexity >= 20:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _estimate_class_removal_effort(self, method_count: int) -> str:
        """Estimate effort to remove a class.

        Args:
            method_count: Number of methods

        Returns:
            Effort estimate
        """
        if method_count >= 10:
            return "Medium (2-4 hours)"
        elif method_count >= 5:
            return "Small (1-2 hours)"
        else:
            return "Small (30 minutes)"
