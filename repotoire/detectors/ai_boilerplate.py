"""AI Boilerplate Explosion detector - identifies excessive boilerplate code.

AI assistants often generate verbose, repetitive code that could be abstracted
into reusable patterns. This detector catches:
- Multiple functions with similar structure (same parameter patterns, similar bodies)
- Repeated error handling patterns
- Copy-paste API endpoints with minor variations
- Repeated setup/teardown patterns

REPO-XXX: AI-generated code quality detection.
"""

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


@dataclass
class FunctionSignature:
    """Represents a function's structural signature for comparison."""
    
    qualified_name: str
    file_path: str
    param_count: int
    param_types_hash: str  # Hash of sorted parameter types
    return_type: Optional[str]
    decorators: FrozenSet[str]
    is_async: bool
    has_try_except: bool
    body_structure_hash: str  # Hash of AST-like body structure
    line_count: int
    complexity: int


@dataclass
class SimilarityGroup:
    """A group of functions that share structural similarity."""
    
    functions: List[FunctionSignature]
    similarity_score: float  # 0.0-1.0
    pattern_type: str  # "parameter", "error_handling", "decorator", "body_structure"
    abstraction_suggestion: str


class AIBoilerplateDetector(CodeSmellDetector):
    """Detects excessive boilerplate code that could be abstracted.
    
    AI assistants often generate verbose, repetitive code patterns:
    - Multiple functions with identical parameter signatures
    - Repeated try/except blocks with similar handling
    - Copy-paste API endpoints with minor variations
    - Redundant setup/teardown patterns
    
    Example finding:
        5 functions share the same (user_id, session_id, request) parameters
        and similar error handling. Consider extracting to a base handler or decorator.
    """
    
    THRESHOLDS = {
        "min_group_size": 3,           # Minimum similar functions to report
        "param_similarity_threshold": 0.8,  # 80% parameter overlap
        "body_similarity_threshold": 0.7,   # 70% structural similarity
    }
    
    # Common AI-generated patterns to detect
    BOILERPLATE_PATTERNS = {
        "crud_endpoints": ["create", "read", "update", "delete", "get", "list", "fetch"],
        "error_handlers": ["try", "except", "catch", "handle", "error"],
        "validation": ["validate", "check", "verify", "ensure", "assert"],
        "logging": ["log", "debug", "info", "warn", "error", "trace"],
    }
    
    # Decorator patterns that suggest abstraction potential
    ABSTRACTION_DECORATORS = {
        frozenset({"app.route", "router.get", "router.post", "api_view"}): "API endpoint decorator/base class",
        frozenset({"login_required", "auth_required", "requires_auth"}): "Authentication mixin",
        frozenset({"cache", "cached", "lru_cache", "memoize"}): "Caching decorator",
        frozenset({"retry", "with_retry", "backoff"}): "Retry decorator",
        frozenset({"transaction", "atomic", "db_session"}): "Database transaction decorator",
    }
    
    def __init__(
        self,
        graph_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize AI boilerplate detector.
        
        Args:
            graph_client: FalkorDB database client
            detector_config: Optional detector configuration
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        
        # Allow config to override thresholds
        config = detector_config or {}
        self.min_group_size = config.get("min_group_size", self.THRESHOLDS["min_group_size"])
        self.param_similarity_threshold = config.get(
            "param_similarity_threshold", 
            self.THRESHOLDS["param_similarity_threshold"]
        )
        self.body_similarity_threshold = config.get(
            "body_similarity_threshold",
            self.THRESHOLDS["body_similarity_threshold"]
        )
    
    def detect(self) -> List[Finding]:
        """Detect AI-generated boilerplate patterns across the codebase.
        
        Returns:
            List of findings for detected boilerplate patterns
        """
        logger.info("Running AIBoilerplateDetector")
        
        # Get all functions with their structural information
        functions = self._get_function_signatures()
        
        if len(functions) < self.min_group_size:
            logger.info(f"Found only {len(functions)} functions, need at least {self.min_group_size}")
            return []
        
        logger.debug(f"Analyzing {len(functions)} functions for boilerplate patterns")
        
        # Find similarity groups
        findings = []
        
        # 1. Parameter pattern similarity (same param signatures)
        param_groups = self._find_parameter_pattern_groups(functions)
        for group in param_groups:
            finding = self._create_finding(group)
            findings.append(finding)
        
        # 2. Error handling similarity (same try/except patterns)
        error_groups = self._find_error_handling_groups(functions)
        for group in error_groups:
            # Avoid duplicating groups already found by parameter matching
            if not self._overlaps_existing_finding(group, findings):
                finding = self._create_finding(group)
                findings.append(finding)
        
        # 3. Decorator pattern similarity
        decorator_groups = self._find_decorator_pattern_groups(functions)
        for group in decorator_groups:
            if not self._overlaps_existing_finding(group, findings):
                finding = self._create_finding(group)
                findings.append(finding)
        
        # 4. Body structure similarity (similar AST patterns)
        body_groups = self._find_body_structure_groups(functions)
        for group in body_groups:
            if not self._overlaps_existing_finding(group, findings):
                finding = self._create_finding(group)
                findings.append(finding)
        
        logger.info(f"Found {len(findings)} boilerplate pattern(s)")
        return findings
    
    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on group size and pattern type.
        
        Args:
            finding: Finding to assess
            
        Returns:
            Severity level
        """
        group_size = finding.graph_context.get("group_size", 0)
        abstraction_potential = finding.graph_context.get("abstraction_potential", 0.0)
        
        # Large groups with high abstraction potential are more severe
        if group_size >= 6 and abstraction_potential >= 0.8:
            return Severity.HIGH
        elif group_size >= 4 or abstraction_potential >= 0.7:
            return Severity.MEDIUM
        return Severity.LOW
    
    def _get_function_signatures(self) -> List[FunctionSignature]:
        """Get all functions with their structural signatures.
        
        Returns:
            List of FunctionSignature objects
        """
        repo_filter = self._get_isolation_filter("f")
        query = f"""
        MATCH (f:Function)
        WHERE true {repo_filter}
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        RETURN 
            f.qualifiedName AS name,
            f.parameters AS params,
            f.parameterTypes AS paramTypes,
            f.returnType AS returnType,
            f.decorators AS decorators,
            f.isAsync AS isAsync,
            f.sourceCode AS sourceCode,
            f.lineStart AS lineStart,
            f.lineEnd AS lineEnd,
            f.complexity AS complexity,
            file.filePath AS filePath
        """
        
        results = self.db.execute_query(query, self._get_query_params())
        
        signatures = []
        for row in results:
            sig = self._build_signature(row)
            if sig:
                signatures.append(sig)
        
        return signatures
    
    def _build_signature(self, row: Dict) -> Optional[FunctionSignature]:
        """Build a FunctionSignature from query result row.
        
        Args:
            row: Query result row
            
        Returns:
            FunctionSignature or None if insufficient data
        """
        name = row.get("name")
        if not name:
            return None
        
        # Extract parameters
        params = row.get("params", []) or []
        param_count = len([p for p in params if p not in ("self", "cls")])
        
        # Build parameter types hash for comparison
        param_types = row.get("paramTypes", {}) or {}
        if isinstance(param_types, dict):
            types_str = ",".join(sorted(f"{k}:{v}" for k, v in param_types.items()))
        else:
            types_str = ""
        param_types_hash = hashlib.md5(types_str.encode()).hexdigest()[:8]
        
        # Process decorators
        decorators_raw = row.get("decorators", []) or []
        decorators = frozenset(str(d) for d in decorators_raw)
        
        # Analyze source code for patterns
        source = row.get("sourceCode", "") or ""
        has_try_except = self._has_try_except_pattern(source)
        body_structure_hash = self._compute_body_hash(source)
        
        # Calculate line count
        line_start = row.get("lineStart", 0) or 0
        line_end = row.get("lineEnd", 0) or 0
        line_count = max(0, line_end - line_start + 1)
        
        return FunctionSignature(
            qualified_name=name,
            file_path=row.get("filePath", ""),
            param_count=param_count,
            param_types_hash=param_types_hash,
            return_type=row.get("returnType"),
            decorators=decorators,
            is_async=row.get("isAsync", False) or False,
            has_try_except=has_try_except,
            body_structure_hash=body_structure_hash,
            line_count=line_count,
            complexity=row.get("complexity", 0) or 0,
        )
    
    def _has_try_except_pattern(self, source: str) -> bool:
        """Check if source contains try/except pattern.
        
        Args:
            source: Function source code
            
        Returns:
            True if contains try/except pattern
        """
        if not source:
            return False
        return bool(re.search(r'\btry\s*:', source) and re.search(r'\bexcept\b', source))
    
    def _compute_body_hash(self, source: str) -> str:
        """Compute a structural hash of the function body.
        
        Normalizes the source to ignore variable names and focus on structure.
        
        Args:
            source: Function source code
            
        Returns:
            Hash string representing body structure
        """
        if not source:
            return ""
        
        # Normalize: remove comments, strings, whitespace variations
        normalized = source
        
        # Remove string literals
        normalized = re.sub(r'["\'][^"\']*["\']', '""', normalized)
        
        # Remove comments
        normalized = re.sub(r'#.*$', '', normalized, flags=re.MULTILINE)
        
        # Normalize variable names (replace with placeholders)
        # This helps detect structurally similar code with different var names
        normalized = re.sub(r'\b[a-z_][a-z0-9_]*\b', 'VAR', normalized, flags=re.IGNORECASE)
        
        # Normalize whitespace
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        return hashlib.md5(normalized.encode()).hexdigest()[:12]
    
    def _find_parameter_pattern_groups(
        self, 
        functions: List[FunctionSignature]
    ) -> List[SimilarityGroup]:
        """Find groups of functions with similar parameter patterns.
        
        Args:
            functions: List of function signatures
            
        Returns:
            List of SimilarityGroup objects
        """
        # Group by (param_count, param_types_hash)
        param_groups: Dict[Tuple[int, str], List[FunctionSignature]] = defaultdict(list)
        
        for func in functions:
            if func.param_count >= 2:  # Only consider functions with 2+ params
                key = (func.param_count, func.param_types_hash)
                param_groups[key].append(func)
        
        groups = []
        for key, funcs in param_groups.items():
            if len(funcs) >= self.min_group_size:
                # Calculate abstraction potential based on similarity
                similarity = self._calculate_group_similarity(funcs)
                
                groups.append(SimilarityGroup(
                    functions=funcs,
                    similarity_score=similarity,
                    pattern_type="parameter",
                    abstraction_suggestion=self._suggest_parameter_abstraction(funcs),
                ))
        
        return groups
    
    def _find_error_handling_groups(
        self,
        functions: List[FunctionSignature]
    ) -> List[SimilarityGroup]:
        """Find groups of functions with similar error handling.
        
        Args:
            functions: List of function signatures
            
        Returns:
            List of SimilarityGroup objects
        """
        # Filter to functions with try/except
        error_funcs = [f for f in functions if f.has_try_except]
        
        if len(error_funcs) < self.min_group_size:
            return []
        
        # Group by body structure hash (catches similar try/except patterns)
        body_groups: Dict[str, List[FunctionSignature]] = defaultdict(list)
        
        for func in error_funcs:
            if func.body_structure_hash:
                body_groups[func.body_structure_hash].append(func)
        
        groups = []
        for hash_key, funcs in body_groups.items():
            if len(funcs) >= self.min_group_size:
                groups.append(SimilarityGroup(
                    functions=funcs,
                    similarity_score=0.85,  # High similarity for matching hashes
                    pattern_type="error_handling",
                    abstraction_suggestion=self._suggest_error_handling_abstraction(funcs),
                ))
        
        return groups
    
    def _find_decorator_pattern_groups(
        self,
        functions: List[FunctionSignature]
    ) -> List[SimilarityGroup]:
        """Find groups of functions sharing similar decorators.
        
        Args:
            functions: List of function signatures
            
        Returns:
            List of SimilarityGroup objects
        """
        # Group by decorator set
        decorator_groups: Dict[FrozenSet[str], List[FunctionSignature]] = defaultdict(list)
        
        for func in functions:
            if func.decorators:
                decorator_groups[func.decorators].append(func)
        
        groups = []
        for decorators, funcs in decorator_groups.items():
            if len(funcs) >= self.min_group_size:
                # Check if these are abstraction-worthy decorator patterns
                suggestion = self._suggest_decorator_abstraction(decorators, funcs)
                if suggestion:
                    groups.append(SimilarityGroup(
                        functions=funcs,
                        similarity_score=0.9,
                        pattern_type="decorator",
                        abstraction_suggestion=suggestion,
                    ))
        
        return groups
    
    def _find_body_structure_groups(
        self,
        functions: List[FunctionSignature]
    ) -> List[SimilarityGroup]:
        """Find groups of functions with similar body structure.
        
        Args:
            functions: List of function signatures
            
        Returns:
            List of SimilarityGroup objects
        """
        # Group by body structure hash
        body_groups: Dict[str, List[FunctionSignature]] = defaultdict(list)
        
        for func in functions:
            if func.body_structure_hash and func.line_count >= 5:  # Ignore tiny functions
                body_groups[func.body_structure_hash].append(func)
        
        groups = []
        for hash_key, funcs in body_groups.items():
            if len(funcs) >= self.min_group_size:
                # Skip if already covered by error handling
                if all(f.has_try_except for f in funcs):
                    continue
                
                groups.append(SimilarityGroup(
                    functions=funcs,
                    similarity_score=0.8,
                    pattern_type="body_structure",
                    abstraction_suggestion=self._suggest_body_abstraction(funcs),
                ))
        
        return groups
    
    def _calculate_group_similarity(self, funcs: List[FunctionSignature]) -> float:
        """Calculate overall similarity score for a group of functions.
        
        Args:
            funcs: List of function signatures
            
        Returns:
            Similarity score 0.0-1.0
        """
        if len(funcs) < 2:
            return 0.0
        
        scores = []
        
        # Compare all pairs
        for f1, f2 in combinations(funcs, 2):
            pair_score = 0.0
            count = 0
            
            # Parameter similarity
            if f1.param_count == f2.param_count and f1.param_types_hash == f2.param_types_hash:
                pair_score += 1.0
                count += 1
            
            # Return type similarity
            if f1.return_type == f2.return_type:
                pair_score += 0.5
                count += 0.5
            
            # Async similarity
            if f1.is_async == f2.is_async:
                pair_score += 0.25
                count += 0.25
            
            # Body structure similarity
            if f1.body_structure_hash and f1.body_structure_hash == f2.body_structure_hash:
                pair_score += 1.0
                count += 1
            
            if count > 0:
                scores.append(pair_score / count)
        
        return sum(scores) / len(scores) if scores else 0.0
    
    def _suggest_parameter_abstraction(self, funcs: List[FunctionSignature]) -> str:
        """Suggest abstraction for parameter pattern.
        
        Args:
            funcs: Functions in the group
            
        Returns:
            Suggestion string
        """
        if all(f.is_async for f in funcs):
            return (
                "These async functions share identical parameter signatures. Consider:\n"
                "1. Create a base async handler class with shared parameters as attributes\n"
                "2. Use a dependency injection pattern to pass common parameters\n"
                "3. Create a dataclass for the shared parameters (e.g., RequestContext)"
            )
        
        return (
            "These functions share identical parameter signatures. Consider:\n"
            "1. Create a dataclass or NamedTuple to bundle the parameters\n"
            "2. Use a decorator to handle common parameter processing\n"
            "3. Extract a base class with shared initialization"
        )
    
    def _suggest_error_handling_abstraction(self, funcs: List[FunctionSignature]) -> str:
        """Suggest abstraction for error handling pattern.
        
        Args:
            funcs: Functions in the group
            
        Returns:
            Suggestion string
        """
        return (
            "These functions have identical try/except patterns. Consider:\n"
            "1. Create an error handling decorator:\n"
            "   @handle_errors(on_error=default_handler)\n"
            "2. Use a context manager for the common error handling\n"
            "3. Create a base class with error handling in a template method"
        )
    
    def _suggest_decorator_abstraction(
        self, 
        decorators: FrozenSet[str],
        funcs: List[FunctionSignature]
    ) -> Optional[str]:
        """Suggest abstraction for decorator pattern.
        
        Args:
            decorators: Set of decorator names
            funcs: Functions in the group
            
        Returns:
            Suggestion string or None if not abstraction-worthy
        """
        # Check for known abstraction patterns
        for pattern_set, suggestion_type in self.ABSTRACTION_DECORATORS.items():
            if decorators & pattern_set:
                return (
                    f"These {len(funcs)} functions share decorators that suggest a common pattern. Consider:\n"
                    f"1. Create a {suggestion_type}\n"
                    f"2. Use class-based views/handlers with shared decorator logic\n"
                    f"3. Create a factory function to generate decorated handlers"
                )
        
        # Generic decorator pattern
        if len(decorators) >= 2:
            return (
                f"These functions share {len(decorators)} decorators. Consider:\n"
                "1. Combine the decorators into a single composite decorator\n"
                "2. Create a class-based approach where decorators are class methods\n"
                "3. Use a registration pattern to apply decorators automatically"
            )
        
        return None
    
    def _suggest_body_abstraction(self, funcs: List[FunctionSignature]) -> str:
        """Suggest abstraction for body structure pattern.
        
        Args:
            funcs: Functions in the group
            
        Returns:
            Suggestion string
        """
        avg_complexity = sum(f.complexity for f in funcs) / len(funcs)
        
        if avg_complexity > 10:
            return (
                "These functions have identical complex structure. Consider:\n"
                "1. Extract the common logic into a shared utility function\n"
                "2. Use the Template Method pattern with a base class\n"
                "3. Create a strategy pattern to parameterize the varying parts"
            )
        
        return (
            "These functions have nearly identical structure. Consider:\n"
            "1. Extract the common pattern into a higher-order function\n"
            "2. Use a factory function to generate the variations\n"
            "3. Consolidate into a single function with a mode/type parameter"
        )
    
    def _overlaps_existing_finding(
        self,
        group: SimilarityGroup,
        findings: List[Finding]
    ) -> bool:
        """Check if a group significantly overlaps with existing findings.
        
        Args:
            group: New similarity group
            findings: Existing findings
            
        Returns:
            True if >50% overlap with an existing finding
        """
        group_names = {f.qualified_name for f in group.functions}
        
        for finding in findings:
            existing_names = set(finding.affected_nodes)
            overlap = len(group_names & existing_names)
            if overlap > len(group_names) * 0.5:
                return True
        
        return False
    
    def _create_finding(self, group: SimilarityGroup) -> Finding:
        """Create a finding from a similarity group.
        
        Args:
            group: SimilarityGroup to convert
            
        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        
        # Calculate abstraction potential
        abstraction_potential = group.similarity_score * (len(group.functions) / 10)
        abstraction_potential = min(1.0, abstraction_potential)  # Cap at 1.0
        
        # Determine severity
        if len(group.functions) >= 6 and abstraction_potential >= 0.8:
            severity = Severity.HIGH
        elif len(group.functions) >= 4 or abstraction_potential >= 0.7:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW
        
        # Build title and description
        pattern_names = {
            "parameter": "parameter signature",
            "error_handling": "error handling",
            "decorator": "decorator",
            "body_structure": "code structure",
        }
        pattern_display = pattern_names.get(group.pattern_type, group.pattern_type)
        
        func_names = sorted([f.qualified_name for f in group.functions])
        if len(func_names) > 5:
            func_display = ", ".join(func_names[:5]) + f" ... and {len(func_names) - 5} more"
        else:
            func_display = ", ".join(func_names)
        
        title = f"Boilerplate: {len(group.functions)} functions with same {pattern_display}"
        
        description = (
            f"{len(group.functions)} functions share identical {pattern_display} patterns. "
            f"This repetition suggests AI-generated boilerplate that could be abstracted "
            f"into a reusable component, reducing code duplication and improving maintainability.\n\n"
            f"Affected functions: {func_display}\n\n"
            f"Abstraction potential: {abstraction_potential:.0%}"
        )
        
        # Get unique file paths
        file_paths = list({f.file_path for f in group.functions if f.file_path})
        
        finding = Finding(
            id=finding_id,
            detector="AIBoilerplateDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=func_names,
            affected_files=file_paths,
            graph_context={
                "group_size": len(group.functions),
                "pattern_type": group.pattern_type,
                "similarity_score": group.similarity_score,
                "abstraction_potential": abstraction_potential,
                "functions": func_names,
            },
            suggested_fix=group.abstraction_suggestion,
            estimated_effort=self._estimate_effort(len(group.functions)),
            created_at=datetime.now(),
            why_it_matters=(
                "Repeated boilerplate code increases maintenance burden. "
                "When the pattern needs to change, you must update every copy. "
                "Abstracting common patterns improves consistency and reduces bugs."
            ),
        )
        
        # Add collaboration metadata
        confidence = 0.7 + (abstraction_potential * 0.2)
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="AIBoilerplateDetector",
            confidence=confidence,
            evidence=[f"pattern_{group.pattern_type}", f"group_size_{len(group.functions)}"],
            tags=["boilerplate", "ai_generated", "refactoring", group.pattern_type],
        ))
        
        # Flag entities for cross-detector collaboration
        if self.enricher:
            for func in group.functions:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=func.qualified_name,
                        detector="AIBoilerplateDetector",
                        severity=severity.value,
                        issues=["boilerplate", group.pattern_type],
                        confidence=confidence,
                        metadata={
                            "pattern_type": group.pattern_type,
                            "group_size": len(group.functions),
                            "abstraction_potential": abstraction_potential,
                        }
                    )
                except Exception:
                    pass  # Don't fail detection if enrichment fails
        
        return finding
    
    def _estimate_effort(self, group_size: int) -> str:
        """Estimate effort to refactor based on group size.
        
        Args:
            group_size: Number of functions to refactor
            
        Returns:
            Effort estimate string
        """
        if group_size >= 8:
            return "Large (1-2 days)"
        elif group_size >= 5:
            return "Medium (4-8 hours)"
        else:
            return "Small (2-4 hours)"
