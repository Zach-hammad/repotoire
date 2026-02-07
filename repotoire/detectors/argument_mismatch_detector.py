"""Argument mismatch detector - identifies potential call signature issues.

Detects functions with risky parameter signatures and potential override
mismatches that could lead to runtime errors.

NOTE: Full call-site argument mismatch detection requires storing arg_count
and kwarg_count on CALLS relationships. This detector currently uses
signature-based analysis as a proxy for likely issues.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity


class ArgumentMismatchDetector(CodeSmellDetector):
    """Detects potential argument mismatch issues via signature analysis.
    
    Since call-site argument counts are not stored in the graph, this detector
    focuses on identifying risky function signatures and override mismatches:
    
    1. Functions with many required parameters (high error risk)
    2. Override methods with different signatures than parent
    3. Functions called frequently with complex signatures
    
    Severity: HIGH - argument mismatches cause runtime errors.
    """
    
    # Detection thresholds
    DEFAULT_THRESHOLDS = {
        "max_required_params": 5,    # Functions with more are high-risk
        "min_callers_for_risk": 3,   # Only flag if called by multiple functions
    }
    
    # Patterns to exclude (special methods, test functions)
    EXCLUDE_PATTERNS = [
        "__init__",  # Constructors often have many params
        "__new__",
        "test_",     # Test functions
        "_test",
        "mock_",
    ]
    
    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize argument mismatch detector.
        
        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with thresholds
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.logger = get_logger(__name__)
        
        config = detector_config or {}
        self.max_required_params = config.get(
            "max_required_params",
            self.DEFAULT_THRESHOLDS["max_required_params"]
        )
        self.min_callers_for_risk = config.get(
            "min_callers_for_risk",
            self.DEFAULT_THRESHOLDS["min_callers_for_risk"]
        )
    
    def detect(self) -> List[Finding]:
        """Detect potential argument mismatch issues.
        
        Returns:
            List of findings for potential argument mismatches
        """
        # Fast path: use QueryCache if available
        if self.query_cache is not None:
            self.logger.debug("Using QueryCache for argument mismatch detection")
            return self._detect_cached()
        
        findings = []
        
        # Detection 1: Functions with many required parameters + multiple callers
        findings.extend(self._detect_risky_signatures())
        
        # Detection 2: Override signature mismatches
        findings.extend(self._detect_override_mismatches())
        
        self.logger.info(
            f"ArgumentMismatchDetector found {len(findings)} potential issues"
        )
        return findings
    
    def _detect_risky_signatures(self) -> List[Finding]:
        """Detect functions with risky parameter signatures.
        
        Flags functions that:
        - Have many parameters (>= max_required_params)
        - Are called by multiple functions (>= min_callers_for_risk)
        - Don't use *args/**kwargs (rigid signature)
        
        Returns:
            List of findings for risky signatures
        """
        # REPO-600: Filter by tenant_id AND repo_id
        repo_filter = self._get_isolation_filter("f")
        
        query = f"""
        MATCH (f:Function)
        WHERE f.qualifiedName IS NOT NULL
          AND f.parameters IS NOT NULL
          AND size(f.parameters) >= $max_params
          {repo_filter}
        
        // Count callers
        OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
        WITH f, count(DISTINCT caller) AS caller_count
        WHERE caller_count >= $min_callers
        
        // Get file path
        OPTIONAL MATCH (f)<-[:CONTAINS*]-(file:File)
        
        RETURN 
            f.qualifiedName AS qualified_name,
            f.name AS func_name,
            f.parameters AS parameters,
            f.lineStart AS line_start,
            f.lineEnd AS line_end,
            f.filePath AS file_path,
            file.filePath AS containing_file,
            caller_count
        ORDER BY caller_count DESC, size(f.parameters) DESC
        LIMIT 50
        """
        
        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(
                    max_params=self.max_required_params,
                    min_callers=self.min_callers_for_risk,
                ),
            )
        except Exception as e:
            self.logger.error(f"Error in risky signatures query: {e}")
            return []
        
        findings = []
        for row in results:
            func_name = row.get("func_name", "")
            
            # Skip excluded patterns
            if self._should_exclude(func_name):
                continue
            
            parameters = row.get("parameters", []) or []
            
            # Skip if has *args or **kwargs (flexible signature)
            if self._has_variadic_params(parameters):
                continue
            
            # Count required params (those without default values)
            required_count = self._count_required_params(parameters)
            
            if required_count >= self.max_required_params:
                finding = self._create_risky_signature_finding(row, required_count)
                findings.append(finding)
                
                # Flag entity for cross-detector collaboration
                if self.enricher and row.get("qualified_name"):
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=row["qualified_name"],
                            detector="ArgumentMismatchDetector",
                            severity=finding.severity.value,
                            issues=["risky_signature", "many_required_params"],
                            confidence=0.7,
                            metadata={
                                "param_count": len(parameters),
                                "required_count": required_count,
                                "caller_count": row.get("caller_count", 0),
                            },
                        )
                    except Exception:
                        pass
        
        return findings
    
    def _detect_override_mismatches(self) -> List[Finding]:
        """Detect methods that override parent with different signatures.
        
        Flags child methods where parameter count differs from parent method.
        
        Returns:
            List of findings for override mismatches
        """
        # REPO-600: Filter by tenant_id AND repo_id
        repo_filter = self._get_isolation_filter("child")
        
        query = f"""
        MATCH (childClass:Class)-[:INHERITS]->(parentClass:Class)
        MATCH (childClass)-[:CONTAINS]->(childMethod:Function)
        MATCH (parentClass)-[:CONTAINS]->(parentMethod:Function)
        WHERE childMethod.name = parentMethod.name
          AND childMethod.name IS NOT NULL
          AND childMethod.parameters IS NOT NULL
          AND parentMethod.parameters IS NOT NULL
          AND size(childMethod.parameters) <> size(parentMethod.parameters)
          {repo_filter}
        
        // Get file path
        OPTIONAL MATCH (childMethod)<-[:CONTAINS*]-(file:File)
        
        RETURN 
            childMethod.qualifiedName AS qualified_name,
            childMethod.name AS method_name,
            childMethod.parameters AS child_params,
            childMethod.lineStart AS line_start,
            childMethod.lineEnd AS line_end,
            parentMethod.qualifiedName AS parent_qualified_name,
            parentMethod.parameters AS parent_params,
            childClass.name AS child_class,
            parentClass.name AS parent_class,
            file.filePath AS file_path
        LIMIT 50
        """
        
        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(),
            )
        except Exception as e:
            self.logger.error(f"Error in override mismatch query: {e}")
            return []
        
        findings = []
        for row in results:
            method_name = row.get("method_name", "")
            
            # Skip dunder methods and excluded patterns
            if method_name.startswith("__") or self._should_exclude(method_name):
                continue
            
            child_params = row.get("child_params", []) or []
            parent_params = row.get("parent_params", []) or []
            
            # Skip if both have variadic params (likely intentional)
            if (self._has_variadic_params(child_params) and 
                self._has_variadic_params(parent_params)):
                continue
            
            finding = self._create_override_mismatch_finding(row)
            findings.append(finding)
            
            # Flag entity for cross-detector collaboration
            if self.enricher and row.get("qualified_name"):
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=row["qualified_name"],
                        detector="ArgumentMismatchDetector",
                        severity=finding.severity.value,
                        issues=["override_signature_mismatch"],
                        confidence=0.85,
                        metadata={
                            "child_param_count": len(child_params),
                            "parent_param_count": len(parent_params),
                            "parent_method": row.get("parent_qualified_name"),
                        },
                    )
                except Exception:
                    pass
        
        return findings
    
    def _detect_cached(self) -> List[Finding]:
        """Detect argument mismatches using QueryCache.
        
        O(1) lookup from prefetched data instead of database queries.
        
        Returns:
            List of findings
        """
        findings = []
        
        # Detection 1: Functions with risky signatures
        for func_name, func_data in self.query_cache.functions.items():
            simple_name = func_name.split(".")[-1]
            
            # Skip excluded patterns
            if self._should_exclude(simple_name):
                continue
            
            parameters = func_data.parameters or []
            
            # Skip if has variadic params
            if self._has_variadic_params(parameters):
                continue
            
            # Check parameter count
            required_count = self._count_required_params(parameters)
            if required_count < self.max_required_params:
                continue
            
            # Check caller count
            callers = self.query_cache.get_callers(func_name)
            if len(callers) < self.min_callers_for_risk:
                continue
            
            # Build row dict
            row = {
                "qualified_name": func_name,
                "func_name": simple_name,
                "parameters": parameters,
                "line_start": func_data.line_start,
                "line_end": func_data.line_end,
                "file_path": func_data.file_path,
                "containing_file": func_data.file_path,
                "caller_count": len(callers),
            }
            
            finding = self._create_risky_signature_finding(row, required_count)
            findings.append(finding)
        
        # Detection 2: Override signature mismatches
        for child_class, parents in self.query_cache.inherits.items():
            child_methods = self.query_cache.get_methods(child_class)
            
            for parent_class in parents:
                parent_methods = self.query_cache.get_methods(parent_class)
                
                # Find methods with same name
                for child_method in child_methods:
                    child_simple = child_method.split(".")[-1]
                    
                    # Skip dunder and excluded
                    if child_simple.startswith("__") or self._should_exclude(child_simple):
                        continue
                    
                    for parent_method in parent_methods:
                        parent_simple = parent_method.split(".")[-1]
                        
                        if child_simple != parent_simple:
                            continue
                        
                        # Get function data
                        child_func = self.query_cache.get_function(child_method)
                        parent_func = self.query_cache.get_function(parent_method)
                        
                        if not child_func or not parent_func:
                            continue
                        
                        child_params = child_func.parameters or []
                        parent_params = parent_func.parameters or []
                        
                        # Check for mismatch
                        if len(child_params) == len(parent_params):
                            continue
                        
                        # Skip if both variadic
                        if (self._has_variadic_params(child_params) and
                            self._has_variadic_params(parent_params)):
                            continue
                        
                        row = {
                            "qualified_name": child_method,
                            "method_name": child_simple,
                            "child_params": child_params,
                            "line_start": child_func.line_start,
                            "line_end": child_func.line_end,
                            "parent_qualified_name": parent_method,
                            "parent_params": parent_params,
                            "child_class": child_class.split(".")[-1],
                            "parent_class": parent_class.split(".")[-1],
                            "file_path": child_func.file_path,
                        }
                        
                        finding = self._create_override_mismatch_finding(row)
                        findings.append(finding)
        
        # Sort by severity indicators and limit
        findings.sort(
            key=lambda f: (
                -f.graph_context.get("caller_count", 0),
                -f.graph_context.get("param_count", 0),
            )
        )
        findings = findings[:50]
        
        self.logger.info(
            f"ArgumentMismatchDetector (cached) found {len(findings)} issues"
        )
        return findings
    
    def _should_exclude(self, func_name: str) -> bool:
        """Check if function matches an exclusion pattern.
        
        Args:
            func_name: Name of the function to check
            
        Returns:
            True if function should be excluded
        """
        if not func_name:
            return True
        
        func_lower = func_name.lower()
        for pattern in self.EXCLUDE_PATTERNS:
            if pattern in func_lower:
                return True
        
        return False
    
    def _has_variadic_params(self, parameters: List[str]) -> bool:
        """Check if function has *args or **kwargs.
        
        Args:
            parameters: List of parameter names
            
        Returns:
            True if function accepts variadic arguments
        """
        for param in parameters:
            if param.startswith("*"):
                return True
        return False
    
    def _count_required_params(self, parameters: List[str]) -> int:
        """Count required parameters (no default value).
        
        Heuristic: parameters with '=' in name have defaults.
        Note: This is approximate since we only store param names.
        
        Args:
            parameters: List of parameter names
            
        Returns:
            Count of required parameters
        """
        required = 0
        for param in parameters:
            # Skip self/cls
            if param in ("self", "cls"):
                continue
            # Skip variadic
            if param.startswith("*"):
                continue
            # Assume params without special markers are required
            # (accurate parameter default info would need AST re-parse)
            required += 1
        return required
    
    def _create_risky_signature_finding(
        self,
        row: Dict[str, Any],
        required_count: int,
    ) -> Finding:
        """Create a finding for risky function signature.
        
        Args:
            row: Query result row
            required_count: Number of required parameters
            
        Returns:
            Finding object
        """
        qualified_name = row.get("qualified_name", "unknown")
        func_name = row.get("func_name", qualified_name.split(".")[-1])
        parameters = row.get("parameters", []) or []
        caller_count = row.get("caller_count", 0)
        file_path = row.get("file_path") or row.get("containing_file", "unknown")
        line_start = row.get("line_start")
        line_end = row.get("line_end")
        
        description = (
            f"Function '{func_name}' has {len(parameters)} parameters "
            f"({required_count} required) and is called by {caller_count} "
            f"functions. This complex signature is error-prone and may lead "
            f"to argument mismatches at call sites."
        )
        
        recommendation = (
            "Consider one of the following:\n"
            "1. Use a configuration object/dataclass to group related parameters\n"
            "2. Add **kwargs for optional parameters\n"
            "3. Provide sensible default values for rarely-changed parameters\n"
            "4. Split the function into smaller, focused functions"
        )
        
        finding = Finding(
            id=f"arg_mismatch_risky_sig_{qualified_name}",
            detector="ArgumentMismatchDetector",
            severity=Severity.HIGH,
            title=f"Risky signature: {func_name} ({required_count} required params)",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort="Medium (1-2 hours)",
            graph_context={
                "param_count": len(parameters),
                "required_count": required_count,
                "caller_count": caller_count,
                "parameters": parameters[:10],  # Truncate for display
            },
        )
        
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="ArgumentMismatchDetector",
            confidence=0.7,
            evidence=["many_required_params", "multiple_callers"],
            tags=["argument_mismatch", "signature_complexity", "maintainability"],
        ))
        
        return finding
    
    def _create_override_mismatch_finding(self, row: Dict[str, Any]) -> Finding:
        """Create a finding for override signature mismatch.
        
        Args:
            row: Query result row
            
        Returns:
            Finding object
        """
        qualified_name = row.get("qualified_name", "unknown")
        method_name = row.get("method_name", qualified_name.split(".")[-1])
        child_params = row.get("child_params", []) or []
        parent_params = row.get("parent_params", []) or []
        child_class = row.get("child_class", "?")
        parent_class = row.get("parent_class", "?")
        parent_qn = row.get("parent_qualified_name", "")
        file_path = row.get("file_path", "unknown")
        line_start = row.get("line_start")
        line_end = row.get("line_end")
        
        description = (
            f"Method '{method_name}' in {child_class} has {len(child_params)} "
            f"parameters but parent method in {parent_class} has "
            f"{len(parent_params)} parameters. This signature mismatch may "
            f"cause runtime errors when the method is called polymorphically."
        )
        
        recommendation = (
            "Ensure the override method signature is compatible with the parent:\n"
            "1. Match the number of required parameters\n"
            "2. Use *args, **kwargs if you need different signatures\n"
            "3. Add default values to additional parameters\n"
            "4. Consider if this should be a separate method, not an override"
        )
        
        affected_nodes = [qualified_name]
        if parent_qn:
            affected_nodes.append(parent_qn)
        
        finding = Finding(
            id=f"arg_mismatch_override_{qualified_name}",
            detector="ArgumentMismatchDetector",
            severity=Severity.HIGH,
            title=f"Override mismatch: {child_class}.{method_name} vs {parent_class}.{method_name}",
            description=description,
            affected_nodes=affected_nodes,
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort="Small (30 minutes)",
            graph_context={
                "child_param_count": len(child_params),
                "parent_param_count": len(parent_params),
                "child_class": child_class,
                "parent_class": parent_class,
                "child_params": child_params[:10],
                "parent_params": parent_params[:10],
            },
        )
        
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="ArgumentMismatchDetector",
            confidence=0.85,
            evidence=["override_param_count_mismatch", "inheritance_chain"],
            tags=["argument_mismatch", "override", "polymorphism", "bug_risk"],
        ))
        
        return finding
    
    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (always HIGH for argument mismatches).
        
        Argument mismatches typically cause runtime errors, making them
        high-severity bugs that should be fixed promptly.
        
        Args:
            finding: Finding to assess
            
        Returns:
            Severity level
        """
        return Severity.HIGH
