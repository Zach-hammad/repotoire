"""AI Boilerplate Explosion detector using AST clustering.

Research-backed approach: Parse functions to normalized ASTs, cluster by
structural similarity, and flag clusters without shared abstractions.

Key insight: AI assistants generate structurally identical code with different
variable names. By normalizing identifiers to TYPE_N placeholders, we can
detect semantic duplicates that simple string matching would miss.

REPO-XXX: AI-generated code quality detection.
"""

import ast
import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


# =============================================================================
# AST Normalization
# =============================================================================

class ASTNormalizer(ast.NodeTransformer):
    """Normalizes AST by replacing identifiers with TYPE_N placeholders.
    
    This allows detection of structurally identical code with different
    variable/function names - a common pattern in AI-generated boilerplate.
    
    Example:
        def get_user(user_id):       def get_order(order_id):
            return db.get(user_id)       return db.get(order_id)
            
    Both normalize to:
        def FUNC_0(VAR_0):
            return ATTR_0.ATTR_1(VAR_0)
    """
    
    def __init__(self):
        self.name_counters: Dict[str, int] = defaultdict(int)
        self.name_map: Dict[str, str] = {}
        super().__init__()
    
    def _get_placeholder(self, category: str, original: str) -> str:
        """Get or create a placeholder for an identifier.
        
        Args:
            category: Type category (VAR, FUNC, ATTR, etc.)
            original: Original identifier name
            
        Returns:
            Normalized placeholder like VAR_0, FUNC_1, etc.
        """
        key = f"{category}:{original}"
        if key not in self.name_map:
            idx = self.name_counters[category]
            self.name_counters[category] += 1
            self.name_map[key] = f"{category}_{idx}"
        return self.name_map[key]
    
    def visit_Name(self, node: ast.Name) -> ast.Name:
        """Normalize variable names."""
        # Preserve builtins and special names
        if node.id in {'True', 'False', 'None', 'self', 'cls', 'super'}:
            return node
        # Preserve common type annotations
        if node.id in {'str', 'int', 'float', 'bool', 'list', 'dict', 'set', 
                       'tuple', 'List', 'Dict', 'Set', 'Tuple', 'Optional',
                       'Union', 'Any', 'Callable', 'Type', 'Sequence'}:
            return node
        
        node.id = self._get_placeholder("VAR", node.id)
        return node
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        """Normalize function definitions."""
        # Normalize function name (but keep __special__ names)
        if not (node.name.startswith('__') and node.name.endswith('__')):
            node.name = self._get_placeholder("FUNC", node.name)
        
        # Normalize argument names
        for arg in node.args.args:
            if arg.arg not in {'self', 'cls'}:
                arg.arg = self._get_placeholder("ARG", arg.arg)
        for arg in node.args.kwonlyargs:
            arg.arg = self._get_placeholder("ARG", arg.arg)
        if node.args.vararg:
            node.args.vararg.arg = self._get_placeholder("ARG", node.args.vararg.arg)
        if node.args.kwarg:
            node.args.kwarg.arg = self._get_placeholder("ARG", node.args.kwarg.arg)
        
        # Remove docstring
        if (node.body and isinstance(node.body[0], ast.Expr) and
            isinstance(node.body[0].value, (ast.Str, ast.Constant))):
            # Check if it's a string constant (docstring)
            val = node.body[0].value
            if isinstance(val, ast.Str) or (isinstance(val, ast.Constant) and isinstance(val.value, str)):
                node.body = node.body[1:] if len(node.body) > 1 else [ast.Pass()]
        
        # Process body
        self.generic_visit(node)
        return node
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        """Normalize async function definitions (same as sync)."""
        # Normalize function name
        if not (node.name.startswith('__') and node.name.endswith('__')):
            node.name = self._get_placeholder("FUNC", node.name)
        
        # Normalize arguments
        for arg in node.args.args:
            if arg.arg not in {'self', 'cls'}:
                arg.arg = self._get_placeholder("ARG", arg.arg)
        for arg in node.args.kwonlyargs:
            arg.arg = self._get_placeholder("ARG", arg.arg)
        if node.args.vararg:
            node.args.vararg.arg = self._get_placeholder("ARG", node.args.vararg.arg)
        if node.args.kwarg:
            node.args.kwarg.arg = self._get_placeholder("ARG", node.args.kwarg.arg)
        
        # Remove docstring
        if (node.body and isinstance(node.body[0], ast.Expr) and
            isinstance(node.body[0].value, (ast.Str, ast.Constant))):
            val = node.body[0].value
            if isinstance(val, ast.Str) or (isinstance(val, ast.Constant) and isinstance(val.value, str)):
                node.body = node.body[1:] if len(node.body) > 1 else [ast.Pass()]
        
        self.generic_visit(node)
        return node
    
    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        """Normalize attribute access (but preserve structure)."""
        self.generic_visit(node)
        # Don't normalize common method names that indicate patterns
        if node.attr not in {'append', 'extend', 'get', 'set', 'update', 'delete',
                            'create', 'read', 'write', 'close', 'open', 'save',
                            'load', 'items', 'keys', 'values', 'format', 'join',
                            'split', 'strip', 'lower', 'upper', 'replace'}:
            node.attr = self._get_placeholder("ATTR", node.attr)
        return node
    
    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        """Normalize string/numeric constants."""
        if isinstance(node.value, str) and len(node.value) > 0:
            # Preserve empty strings and single chars, normalize others
            if len(node.value) > 1:
                node.value = "STR"
        elif isinstance(node.value, (int, float)) and node.value not in {0, 1, -1}:
            node.value = 0  # Normalize non-trivial numbers
        return node
    
    def visit_Str(self, node: ast.Str) -> ast.Str:
        """Normalize string literals (Python < 3.8 compat)."""
        if len(node.s) > 1:
            node.s = "STR"
        return node
    
    def visit_Num(self, node: ast.Num) -> ast.Num:
        """Normalize numeric literals (Python < 3.8 compat)."""
        if node.n not in {0, 1, -1}:
            node.n = 0
        return node


def normalize_ast(source: str) -> Optional[ast.AST]:
    """Parse and normalize source code to canonical AST.
    
    Args:
        source: Python source code string
        
    Returns:
        Normalized AST or None if parsing fails
    """
    if not source:
        return None
    
    try:
        tree = ast.parse(source)
        normalizer = ASTNormalizer()
        normalized = normalizer.visit(tree)
        ast.fix_missing_locations(normalized)
        return normalized
    except (SyntaxError, ValueError, TypeError):
        return None


def serialize_ast(node: ast.AST) -> str:
    """Serialize AST to a canonical string representation.
    
    Uses ast.dump with consistent formatting for comparison.
    
    Args:
        node: AST node to serialize
        
    Returns:
        Canonical string representation
    """
    try:
        return ast.dump(node, annotate_fields=False, include_attributes=False)
    except Exception:
        return ""


def hash_ast(node: ast.AST) -> str:
    """Compute hash of serialized AST.
    
    Args:
        node: AST node to hash
        
    Returns:
        MD5 hash of serialized AST
    """
    serialized = serialize_ast(node)
    return hashlib.md5(serialized.encode()).hexdigest()


def compute_ast_similarity(ast1: ast.AST, ast2: ast.AST) -> float:
    """Compute similarity between two ASTs using sequence matching.
    
    Args:
        ast1: First AST
        ast2: Second AST
        
    Returns:
        Similarity ratio (0.0 - 1.0)
    """
    s1 = serialize_ast(ast1)
    s2 = serialize_ast(ast2)
    
    if not s1 or not s2:
        return 0.0
    
    return SequenceMatcher(None, s1, s2).ratio()


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class NormalizedFunction:
    """A function with its normalized AST representation."""
    
    qualified_name: str
    file_path: str
    original_source: str
    normalized_ast: Optional[ast.AST]
    ast_hash: str
    ast_serialized: str
    
    # Metadata for abstraction checking
    decorators: FrozenSet[str]
    parent_class: Optional[str]
    is_async: bool
    param_count: int
    line_count: int
    complexity: int
    
    # Structural features
    has_try_except: bool
    has_return: bool
    has_yield: bool
    call_count: int  # Number of function calls in body


@dataclass
class FunctionCluster:
    """A cluster of structurally similar functions."""
    
    functions: List[NormalizedFunction]
    centroid_hash: str  # Hash of representative function
    avg_similarity: float
    
    # Abstraction analysis
    shared_decorators: FrozenSet[str]
    shared_parent_class: Optional[str]
    has_shared_abstraction: bool
    abstraction_type: Optional[str]  # "decorator", "base_class", "factory", None


# =============================================================================
# Detector Implementation
# =============================================================================

class AIBoilerplateDetector(CodeSmellDetector):
    """Detects AI-generated boilerplate using AST clustering.
    
    Research-backed approach:
    1. Parse ALL functions to normalized AST (identifiers → TYPE_N)
    2. Serialize and hash each function's AST
    3. Cluster functions by AST similarity (>70% threshold)
    4. For clusters with 3+ functions: check for shared abstraction
    5. Flag clusters WITHOUT shared abstraction as boilerplate explosion
    
    This catches:
    - Same try/except structure repeated
    - Same validation logic in multiple places  
    - Same API call patterns with minor variations
    - CRUD operations that could be genericized
    """
    
    THRESHOLDS = {
        "min_cluster_size": 3,           # Min similar functions to report
        "similarity_threshold": 0.70,     # 70% AST similarity
        "min_function_lines": 3,          # Ignore tiny functions
    }
    
    # Patterns that indicate intentional/acceptable repetition
    ACCEPTABLE_PATTERNS = {
        "test_",          # Test functions often repeat structure
        "__init__",       # Constructors are naturally similar
        "__str__",        # Dunder methods have fixed patterns
        "__repr__",
        "setUp",          # Test fixtures
        "tearDown",
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
        self.min_cluster_size = config.get("min_cluster_size", self.THRESHOLDS["min_cluster_size"])
        self.similarity_threshold = config.get("similarity_threshold", self.THRESHOLDS["similarity_threshold"])
        self.min_function_lines = config.get("min_function_lines", self.THRESHOLDS["min_function_lines"])
    
    def detect(self) -> List[Finding]:
        """Detect AI-generated boilerplate patterns using AST clustering.
        
        Returns:
            List of findings for detected boilerplate patterns
        """
        logger.info("Running AIBoilerplateDetector (AST clustering)")
        
        # Step 1: Get all functions and normalize their ASTs
        functions = self._get_normalized_functions()
        
        if len(functions) < self.min_cluster_size:
            logger.info(f"Found only {len(functions)} parseable functions, need at least {self.min_cluster_size}")
            return []
        
        logger.debug(f"Normalized {len(functions)} functions for clustering")
        
        # Step 2: Cluster by AST hash (exact matches)
        exact_clusters = self._cluster_by_hash(functions)
        
        # Step 3: Cluster by AST similarity (>70% threshold)
        similarity_clusters = self._cluster_by_similarity(functions, exact_clusters)
        
        # Step 4: Merge exact and similarity clusters
        all_clusters = exact_clusters + similarity_clusters
        
        # Step 5: Filter to clusters without shared abstraction
        boilerplate_clusters = self._filter_unabstracted_clusters(all_clusters)
        
        # Step 6: Create findings
        findings = []
        for cluster in boilerplate_clusters:
            finding = self._create_finding(cluster)
            findings.append(finding)
        
        logger.info(f"Found {len(findings)} boilerplate explosion pattern(s)")
        return findings
    
    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on cluster size and pattern type.
        
        Args:
            finding: Finding to assess
            
        Returns:
            Severity level
        """
        cluster_size = finding.graph_context.get("cluster_size", 0)
        similarity = finding.graph_context.get("avg_similarity", 0.0)
        
        # Large clusters with high similarity are more severe
        if cluster_size >= 6 and similarity >= 0.85:
            return Severity.HIGH
        elif cluster_size >= 4 or similarity >= 0.80:
            return Severity.MEDIUM
        return Severity.LOW
    
    def _get_normalized_functions(self) -> List[NormalizedFunction]:
        """Get all functions and normalize their ASTs.
        
        Returns:
            List of NormalizedFunction objects
        """
        repo_filter = self._get_isolation_filter("f")
        
        query = f"""
        MATCH (f:Function)
        WHERE true {repo_filter}
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        OPTIONAL MATCH (c:Class)-[:DEFINES]->(f)
        RETURN 
            f.qualifiedName AS name,
            f.sourceCode AS source,
            f.decorators AS decorators,
            f.isAsync AS isAsync,
            f.parameters AS params,
            f.lineStart AS lineStart,
            f.lineEnd AS lineEnd,
            f.complexity AS complexity,
            file.filePath AS filePath,
            c.qualifiedName AS parentClass
        """
        
        results = self.db.execute_query(query, self._get_query_params())
        
        functions = []
        for row in results:
            func = self._build_normalized_function(row)
            if func and self._should_include_function(func):
                functions.append(func)
        
        return functions
    
    def _build_normalized_function(self, row: Dict) -> Optional[NormalizedFunction]:
        """Build a NormalizedFunction from query result row.
        
        Args:
            row: Query result row
            
        Returns:
            NormalizedFunction or None if insufficient data
        """
        name = row.get("name")
        source = row.get("source", "") or ""
        
        if not name or not source:
            return None
        
        # Parse and normalize AST
        normalized_ast = normalize_ast(source)
        if normalized_ast is None:
            return None
        
        ast_serialized = serialize_ast(normalized_ast)
        if not ast_serialized:
            return None
        
        ast_hash = hash_ast(normalized_ast)
        
        # Extract metadata
        decorators_raw = row.get("decorators", []) or []
        decorators = frozenset(str(d) for d in decorators_raw)
        
        params = row.get("params", []) or []
        param_count = len([p for p in params if p not in ("self", "cls")])
        
        line_start = row.get("lineStart", 0) or 0
        line_end = row.get("lineEnd", 0) or 0
        line_count = max(0, line_end - line_start + 1)
        
        # Analyze structure
        has_try_except = "try:" in source and "except" in source
        has_return = bool(re.search(r'\breturn\b', source))
        has_yield = bool(re.search(r'\byield\b', source))
        call_count = source.count('(') - source.count('def ') - source.count('class ')
        
        return NormalizedFunction(
            qualified_name=name,
            file_path=row.get("filePath", "") or "",
            original_source=source,
            normalized_ast=normalized_ast,
            ast_hash=ast_hash,
            ast_serialized=ast_serialized,
            decorators=decorators,
            parent_class=row.get("parentClass"),
            is_async=row.get("isAsync", False) or False,
            param_count=param_count,
            line_count=line_count,
            complexity=row.get("complexity", 0) or 0,
            has_try_except=has_try_except,
            has_return=has_return,
            has_yield=has_yield,
            call_count=max(0, call_count),
        )
    
    def _should_include_function(self, func: NormalizedFunction) -> bool:
        """Check if function should be included in analysis.
        
        Args:
            func: Function to check
            
        Returns:
            True if function should be analyzed
        """
        # Skip tiny functions
        if func.line_count < self.min_function_lines:
            return False
        
        # Skip acceptable patterns
        func_basename = func.qualified_name.split(".")[-1] if func.qualified_name else ""
        for pattern in self.ACCEPTABLE_PATTERNS:
            if func_basename.startswith(pattern) or func_basename == pattern:
                return False
        
        return True
    
    def _cluster_by_hash(self, functions: List[NormalizedFunction]) -> List[FunctionCluster]:
        """Cluster functions with identical AST hashes.
        
        Args:
            functions: List of normalized functions
            
        Returns:
            List of clusters with exact AST matches
        """
        hash_groups: Dict[str, List[NormalizedFunction]] = defaultdict(list)
        
        for func in functions:
            hash_groups[func.ast_hash].append(func)
        
        clusters = []
        for ast_hash, group in hash_groups.items():
            if len(group) >= self.min_cluster_size:
                cluster = self._analyze_cluster(group, ast_hash, 1.0)
                clusters.append(cluster)
        
        return clusters
    
    def _cluster_by_similarity(
        self,
        functions: List[NormalizedFunction],
        existing_clusters: List[FunctionCluster]
    ) -> List[FunctionCluster]:
        """Cluster functions by AST similarity (>70% threshold).
        
        Uses greedy clustering: for each unclustered function, find similar
        functions and form a cluster if enough are found.
        
        Args:
            functions: List of normalized functions
            existing_clusters: Already-formed exact-match clusters
            
        Returns:
            Additional clusters based on similarity
        """
        # Get functions not already in exact-match clusters
        clustered_names = set()
        for cluster in existing_clusters:
            for func in cluster.functions:
                clustered_names.add(func.qualified_name)
        
        unclustered = [f for f in functions if f.qualified_name not in clustered_names]
        
        if len(unclustered) < self.min_cluster_size:
            return []
        
        # Group by structural features first (optimization)
        # Functions with different structural features won't be similar
        feature_groups: Dict[Tuple, List[NormalizedFunction]] = defaultdict(list)
        
        for func in unclustered:
            # Key: (has_try, has_return, has_yield, param_bucket, async)
            param_bucket = min(func.param_count, 5)  # Bucket params: 0,1,2,3,4,5+
            key = (func.has_try_except, func.has_return, func.has_yield, param_bucket, func.is_async)
            feature_groups[key].append(func)
        
        clusters = []
        processed = set()
        
        for group in feature_groups.values():
            if len(group) < self.min_cluster_size:
                continue
            
            # Within each feature group, find similarity clusters
            for i, func1 in enumerate(group):
                if func1.qualified_name in processed:
                    continue
                
                # Find similar functions
                similar = [func1]
                for j, func2 in enumerate(group):
                    if i == j or func2.qualified_name in processed:
                        continue
                    
                    if func1.normalized_ast and func2.normalized_ast:
                        sim = compute_ast_similarity(func1.normalized_ast, func2.normalized_ast)
                        if sim >= self.similarity_threshold:
                            similar.append(func2)
                
                if len(similar) >= self.min_cluster_size:
                    # Calculate average similarity
                    sims = []
                    for f1, f2 in combinations(similar, 2):
                        if f1.normalized_ast and f2.normalized_ast:
                            sims.append(compute_ast_similarity(f1.normalized_ast, f2.normalized_ast))
                    avg_sim = sum(sims) / len(sims) if sims else self.similarity_threshold
                    
                    cluster = self._analyze_cluster(similar, func1.ast_hash, avg_sim)
                    clusters.append(cluster)
                    
                    # Mark as processed
                    for f in similar:
                        processed.add(f.qualified_name)
        
        return clusters
    
    def _analyze_cluster(
        self,
        functions: List[NormalizedFunction],
        centroid_hash: str,
        avg_similarity: float
    ) -> FunctionCluster:
        """Analyze a cluster for shared abstractions.
        
        Args:
            functions: Functions in the cluster
            centroid_hash: Representative AST hash
            avg_similarity: Average pairwise similarity
            
        Returns:
            FunctionCluster with abstraction analysis
        """
        # Find shared decorators
        if functions:
            shared_decorators = functions[0].decorators
            for func in functions[1:]:
                shared_decorators = shared_decorators & func.decorators
        else:
            shared_decorators = frozenset()
        
        # Find shared parent class
        parent_classes = {f.parent_class for f in functions if f.parent_class}
        shared_parent_class = parent_classes.pop() if len(parent_classes) == 1 else None
        
        # Determine if cluster has a shared abstraction
        has_shared_abstraction = False
        abstraction_type = None
        
        if shared_parent_class:
            has_shared_abstraction = True
            abstraction_type = "base_class"
        elif shared_decorators:
            # Check if decorators indicate an abstraction pattern
            abstraction_decorators = {
                "route", "get", "post", "put", "delete", "patch",  # Web frameworks
                "property", "staticmethod", "classmethod",          # Built-ins
                "abstractmethod", "overload",                       # Typing
                "dataclass", "attrs",                               # Data classes
                "task", "job", "worker",                            # Task queues
                "cached", "cache", "memoize",                       # Caching
            }
            for dec in shared_decorators:
                dec_name = dec.split(".")[-1].lower() if dec else ""
                if dec_name in abstraction_decorators:
                    has_shared_abstraction = True
                    abstraction_type = "decorator"
                    break
        
        return FunctionCluster(
            functions=functions,
            centroid_hash=centroid_hash,
            avg_similarity=avg_similarity,
            shared_decorators=shared_decorators,
            shared_parent_class=shared_parent_class,
            has_shared_abstraction=has_shared_abstraction,
            abstraction_type=abstraction_type,
        )
    
    def _filter_unabstracted_clusters(
        self,
        clusters: List[FunctionCluster]
    ) -> List[FunctionCluster]:
        """Filter to clusters without shared abstractions.
        
        These are the "boilerplate explosions" - similar code that SHOULD
        have been abstracted but wasn't.
        
        Args:
            clusters: All detected clusters
            
        Returns:
            Clusters flagged as boilerplate (no shared abstraction)
        """
        return [c for c in clusters if not c.has_shared_abstraction]
    
    def _create_finding(self, cluster: FunctionCluster) -> Finding:
        """Create a finding from a function cluster.
        
        Args:
            cluster: FunctionCluster to convert
            
        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        
        # Calculate severity
        if len(cluster.functions) >= 6 and cluster.avg_similarity >= 0.85:
            severity = Severity.HIGH
        elif len(cluster.functions) >= 4 or cluster.avg_similarity >= 0.80:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW
        
        # Build function list
        func_names = sorted([f.qualified_name for f in cluster.functions])
        if len(func_names) > 5:
            func_display = ", ".join(func_names[:5]) + f" ... and {len(func_names) - 5} more"
        else:
            func_display = ", ".join(func_names)
        
        # Determine pattern type
        pattern_indicators = []
        if all(f.has_try_except for f in cluster.functions):
            pattern_indicators.append("try/except handling")
        if cluster.avg_similarity >= 0.95:
            pattern_indicators.append("near-identical structure")
        if all(f.has_return for f in cluster.functions):
            # Check for CRUD-like patterns
            names_lower = [f.qualified_name.lower() for f in cluster.functions]
            crud_keywords = {"get", "create", "update", "delete", "list", "fetch", "save"}
            if any(any(kw in name for kw in crud_keywords) for name in names_lower):
                pattern_indicators.append("CRUD operations")
        
        pattern_desc = " with " + ", ".join(pattern_indicators) if pattern_indicators else ""
        
        # Suggest abstraction
        suggestion = self._generate_abstraction_suggestion(cluster)
        
        title = f"Boilerplate explosion: {len(cluster.functions)} functions share {cluster.avg_similarity:.0%} structural similarity"
        
        description = (
            f"**{len(cluster.functions)} functions** have nearly identical AST structure{pattern_desc}.\n\n"
            f"**Similarity:** {cluster.avg_similarity:.0%}\n"
            f"**Functions:** {func_display}\n\n"
            f"This pattern suggests AI-generated boilerplate that was copy-pasted "
            f"with minor modifications instead of being properly abstracted."
        )
        
        # Get unique file paths
        file_paths = list({f.file_path for f in cluster.functions if f.file_path})
        
        finding = Finding(
            id=finding_id,
            detector="AIBoilerplateDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=func_names,
            affected_files=file_paths,
            graph_context={
                "cluster_size": len(cluster.functions),
                "avg_similarity": cluster.avg_similarity,
                "centroid_hash": cluster.centroid_hash,
                "has_try_except": all(f.has_try_except for f in cluster.functions),
                "shared_decorators": list(cluster.shared_decorators),
                "functions": func_names,
            },
            suggested_fix=suggestion,
            estimated_effort=self._estimate_effort(len(cluster.functions)),
            created_at=datetime.now(),
            why_it_matters=(
                "Repeated boilerplate code increases maintenance burden exponentially. "
                "When the pattern needs to change, every copy must be updated. "
                "Abstracting common patterns improves consistency, reduces bugs, "
                "and makes the codebase more maintainable."
            ),
        )
        
        # Add collaboration metadata
        confidence = 0.7 + (cluster.avg_similarity * 0.25)
        confidence = min(0.95, confidence)
        
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="AIBoilerplateDetector",
            confidence=confidence,
            evidence=[
                f"ast_similarity_{cluster.avg_similarity:.0%}",
                f"cluster_size_{len(cluster.functions)}",
            ],
            tags=["boilerplate", "ai_generated", "refactoring", "ast_clustering"],
        ))
        
        # Flag entities for cross-detector collaboration
        if self.enricher:
            for func in cluster.functions:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=func.qualified_name,
                        detector="AIBoilerplateDetector",
                        severity=severity.value,
                        issues=["boilerplate_explosion", "ast_duplicate"],
                        confidence=confidence,
                        metadata={
                            "cluster_size": len(cluster.functions),
                            "avg_similarity": cluster.avg_similarity,
                        }
                    )
                except Exception:
                    pass  # Don't fail detection if enrichment fails
        
        return finding
    
    def _generate_abstraction_suggestion(self, cluster: FunctionCluster) -> str:
        """Generate context-aware abstraction suggestion.
        
        Args:
            cluster: Function cluster to analyze
            
        Returns:
            Detailed suggestion string
        """
        n = len(cluster.functions)
        
        # Check for common patterns
        all_try_except = all(f.has_try_except for f in cluster.functions)
        all_async = all(f.is_async for f in cluster.functions)
        similar_params = len({f.param_count for f in cluster.functions}) == 1
        
        suggestions = []
        
        if all_try_except:
            suggestions.append(
                "**Error handling decorator:**\n"
                "```python\n"
                "@handle_errors(on_error=default_handler)\n"
                "def function(...):\n"
                "    # Just the core logic, no try/except\n"
                "```"
            )
        
        if similar_params:
            param_count = cluster.functions[0].param_count
            if param_count >= 3:
                suggestions.append(
                    "**Parameter dataclass:**\n"
                    "```python\n"
                    "@dataclass\n"
                    "class RequestContext:\n"
                    "    # Bundle common parameters\n"
                    "    ...\n"
                    "```"
                )
        
        if all_async:
            suggestions.append(
                "**Base async handler:**\n"
                "```python\n"
                "class BaseAsyncHandler:\n"
                "    async def handle(self, context): ...\n"
                "```"
            )
        
        # Generic suggestion
        suggestions.append(
            f"**Generic function pattern:**\n"
            f"These {n} functions could be consolidated into a single generic function "
            f"with a mode/type parameter, or into a factory that generates handlers."
        )
        
        return "\n\n".join(suggestions)
    
    def _estimate_effort(self, cluster_size: int) -> str:
        """Estimate effort to refactor based on cluster size.
        
        Args:
            cluster_size: Number of functions to refactor
            
        Returns:
            Effort estimate string
        """
        if cluster_size >= 8:
            return "Large (1-2 days)"
        elif cluster_size >= 5:
            return "Medium (4-8 hours)"
        else:
            return "Small (2-4 hours)"
