"""AI Boilerplate Explosion detector - identifies excessive boilerplate code.

Uses AST-based clustering to find groups of structurally similar functions
that could be abstracted. AI assistants often generate verbose, repetitive
code patterns that should be consolidated.

Research-backed approach (ICSE 2025):
1. Parse all functions to normalized AST
2. Cluster functions by AST similarity (>70% threshold)
3. For clusters with 3+ functions, check for shared abstraction
4. Flag groups lacking abstraction as boilerplate

Key patterns detected:
- Same try/except structure
- Same validation logic
- Same API call patterns with minor variations
- CRUD operations that could be genericized
"""

import ast
import hashlib
import textwrap
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


# ============================================================================
# AST Normalization (shared patterns with ai_duplicate_block)
# ============================================================================

class ASTNormalizer:
    """Normalizes Python AST for similarity comparison.
    
    Replaces identifiers with type-based placeholders:
    - Variables → VAR_N
    - Functions → FUNC_N
    - Classes → CLASS_N
    - Strings/numbers → TYPE placeholder
    """
    
    def __init__(self):
        self.var_counter = 0
        self.func_counter = 0
        self.name_map: Dict[str, str] = {}
        
    def normalize_name(self, name: str, ctx_type: str = "var") -> str:
        """Normalize identifier to type-based placeholder."""
        if name in self.name_map:
            return self.name_map[name]
        
        if ctx_type == "func":
            self.func_counter += 1
            placeholder = f"FUNC_{self.func_counter}"
        else:
            self.var_counter += 1
            placeholder = f"VAR_{self.var_counter}"
        
        self.name_map[name] = placeholder
        return placeholder
    
    def reset(self):
        """Reset for a new function."""
        self.var_counter = 0
        self.func_counter = 0
        self.name_map.clear()


class BoilerplateASTHasher(ast.NodeVisitor):
    """AST visitor that produces normalized hash strings for pattern detection.
    
    Focuses on detecting boilerplate patterns:
    - try/except blocks
    - validation patterns
    - API call patterns
    - CRUD patterns
    """
    
    def __init__(self, normalizer: ASTNormalizer):
        self.normalizer = normalizer
        self.hashes: List[str] = []
        self.patterns: Dict[str, int] = defaultdict(int)  # Pattern type -> count
        
    def _hash(self, s: str) -> str:
        """Create short hash of string."""
        return hashlib.md5(s.encode()).hexdigest()[:8]
    
    def visit_Name(self, node: ast.Name) -> str:
        normalized = self.normalizer.normalize_name(node.id, "var")
        return f"Name({normalized})"
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> str:
        normalized_name = self.normalizer.normalize_name(node.name, "func")
        args = self._visit_arguments(node.args)
        # Skip docstrings
        body = [self.visit(stmt) for stmt in node.body 
                if not (isinstance(stmt, ast.Expr) and 
                       isinstance(stmt.value, ast.Constant) and 
                       isinstance(stmt.value.value, str))]
        decorators = [self.visit(d) for d in node.decorator_list]
        result = f"FunctionDef({normalized_name},{args},{body},{decorators})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> str:
        normalized_name = self.normalizer.normalize_name(node.name, "func")
        args = self._visit_arguments(node.args)
        body = [self.visit(stmt) for stmt in node.body 
                if not (isinstance(stmt, ast.Expr) and 
                       isinstance(stmt.value, ast.Constant) and 
                       isinstance(stmt.value.value, str))]
        decorators = [self.visit(d) for d in node.decorator_list]
        result = f"AsyncFunctionDef({normalized_name},{args},{body},{decorators})"
        self.hashes.append(self._hash(result))
        self.patterns["async"] += 1
        return result
    
    def visit_Try(self, node: ast.Try) -> str:
        """Track try/except pattern."""
        body = [self.visit(stmt) for stmt in node.body]
        handlers = [self._visit_handler(h) for h in node.handlers]
        orelse = [self.visit(stmt) for stmt in node.orelse]
        finalbody = [self.visit(stmt) for stmt in node.finalbody]
        result = f"Try({body},{handlers},{orelse},{finalbody})"
        self.hashes.append(self._hash(result))
        self.patterns["try_except"] += 1
        return result
    
    def visit_If(self, node: ast.If) -> str:
        """Track validation patterns."""
        test = self.visit(node.test)
        body = [self.visit(stmt) for stmt in node.body]
        orelse = [self.visit(stmt) for stmt in node.orelse]
        result = f"If({test},{body},{orelse})"
        self.hashes.append(self._hash(result))
        
        # Detect validation patterns (if not x, raise/return)
        if len(node.body) == 1:
            stmt = node.body[0]
            if isinstance(stmt, (ast.Raise, ast.Return)):
                self.patterns["validation"] += 1
        return result
    
    def visit_Call(self, node: ast.Call) -> str:
        """Track API call patterns."""
        func = self.visit(node.func)
        args = [self.visit(a) for a in node.args]
        keywords = [f"{kw.arg}={self.visit(kw.value)}" for kw in node.keywords]
        result = f"Call({func},{args},{keywords})"
        self.hashes.append(self._hash(result))
        
        # Detect common API patterns
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr.lower()
            if attr in ("get", "post", "put", "delete", "patch"):
                self.patterns["http_method"] += 1
            elif attr in ("query", "execute", "fetch", "find"):
                self.patterns["database"] += 1
            elif attr in ("create", "read", "update", "delete", "list"):
                self.patterns["crud"] += 1
        return result
    
    def visit_With(self, node: ast.With) -> str:
        """Track context manager usage."""
        items = [f"{self.visit(item.context_expr)}as{self.visit(item.optional_vars) if item.optional_vars else 'None'}" 
                 for item in node.items]
        body = [self.visit(stmt) for stmt in node.body]
        result = f"With({items},{body})"
        self.hashes.append(self._hash(result))
        self.patterns["context_manager"] += 1
        return result
    
    def visit_For(self, node: ast.For) -> str:
        target = self.visit(node.target)
        iter_val = self.visit(node.iter)
        body = [self.visit(stmt) for stmt in node.body]
        result = f"For({target},{iter_val},{body})"
        self.hashes.append(self._hash(result))
        self.patterns["loop"] += 1
        return result
    
    def visit_While(self, node: ast.While) -> str:
        test = self.visit(node.test)
        body = [self.visit(stmt) for stmt in node.body]
        result = f"While({test},{body})"
        self.hashes.append(self._hash(result))
        self.patterns["loop"] += 1
        return result
    
    def visit_Return(self, node: ast.Return) -> str:
        value = self.visit(node.value) if node.value else "None"
        result = f"Return({value})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_Assign(self, node: ast.Assign) -> str:
        targets = [self.visit(t) for t in node.targets]
        value = self.visit(node.value)
        result = f"Assign({targets}={value})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_AugAssign(self, node: ast.AugAssign) -> str:
        target = self.visit(node.target)
        op = type(node.op).__name__
        value = self.visit(node.value)
        result = f"AugAssign({target}{op}={value})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_Raise(self, node: ast.Raise) -> str:
        exc = self.visit(node.exc) if node.exc else "None"
        result = f"Raise({exc})"
        self.hashes.append(self._hash(result))
        self.patterns["error_handling"] += 1
        return result
    
    def visit_Attribute(self, node: ast.Attribute) -> str:
        value = self.visit(node.value)
        return f"Attr({value}.{node.attr})"
    
    def visit_Subscript(self, node: ast.Subscript) -> str:
        value = self.visit(node.value)
        slice_val = self.visit(node.slice)
        result = f"Subscript({value}[{slice_val}])"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_BinOp(self, node: ast.BinOp) -> str:
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = type(node.op).__name__
        result = f"BinOp({left}{op}{right})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_Compare(self, node: ast.Compare) -> str:
        left = self.visit(node.left)
        ops = [type(op).__name__ for op in node.ops]
        comparators = [self.visit(c) for c in node.comparators]
        result = f"Compare({left},{ops},{comparators})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_BoolOp(self, node: ast.BoolOp) -> str:
        op = type(node.op).__name__
        values = [self.visit(v) for v in node.values]
        result = f"BoolOp({op},{values})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_UnaryOp(self, node: ast.UnaryOp) -> str:
        op = type(node.op).__name__
        operand = self.visit(node.operand)
        return f"UnaryOp({op},{operand})"
    
    def visit_Constant(self, node: ast.Constant) -> str:
        return f"Const({type(node.value).__name__})"
    
    def visit_List(self, node: ast.List) -> str:
        elts = [self.visit(e) for e in node.elts]
        return f"List({elts})"
    
    def visit_Dict(self, node: ast.Dict) -> str:
        keys = [self.visit(k) if k else "None" for k in node.keys]
        values = [self.visit(v) for v in node.values]
        return f"Dict({keys},{values})"
    
    def visit_Tuple(self, node: ast.Tuple) -> str:
        elts = [self.visit(e) for e in node.elts]
        return f"Tuple({elts})"
    
    def visit_ListComp(self, node: ast.ListComp) -> str:
        elt = self.visit(node.elt)
        generators = [self._visit_comprehension(g) for g in node.generators]
        result = f"ListComp({elt},{generators})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_DictComp(self, node: ast.DictComp) -> str:
        key = self.visit(node.key)
        value = self.visit(node.value)
        generators = [self._visit_comprehension(g) for g in node.generators]
        result = f"DictComp({key}:{value},{generators})"
        self.hashes.append(self._hash(result))
        return result
    
    def visit_Await(self, node: ast.Await) -> str:
        value = self.visit(node.value)
        result = f"Await({value})"
        self.hashes.append(self._hash(result))
        self.patterns["async"] += 1
        return result
    
    def visit_Expr(self, node: ast.Expr) -> str:
        return self.visit(node.value)
    
    def visit_Pass(self, node: ast.Pass) -> str:
        return "Pass"
    
    def visit_Break(self, node: ast.Break) -> str:
        return "Break"
    
    def visit_Continue(self, node: ast.Continue) -> str:
        return "Continue"
    
    def _visit_arguments(self, args: ast.arguments) -> str:
        all_args = []
        for arg in args.posonlyargs + args.args:
            all_args.append(self.normalizer.normalize_name(arg.arg, "var"))
        if args.vararg:
            all_args.append(f"*{self.normalizer.normalize_name(args.vararg.arg, 'var')}")
        for arg in args.kwonlyargs:
            all_args.append(self.normalizer.normalize_name(arg.arg, "var"))
        if args.kwarg:
            all_args.append(f"**{self.normalizer.normalize_name(args.kwarg.arg, 'var')}")
        return f"Args({all_args})"
    
    def _visit_comprehension(self, comp: ast.comprehension) -> str:
        target = self.visit(comp.target)
        iter_val = self.visit(comp.iter)
        ifs = [self.visit(if_clause) for if_clause in comp.ifs]
        return f"Gen({target},{iter_val},{ifs})"
    
    def _visit_handler(self, handler: ast.ExceptHandler) -> str:
        exc_type = handler.type.id if handler.type and isinstance(handler.type, ast.Name) else "Exception"
        if handler.name:
            name = self.normalizer.normalize_name(handler.name, "var")
        else:
            name = "None"
        body = [self.visit(stmt) for stmt in handler.body]
        return f"Handler({exc_type},{name},{body})"
    
    def generic_visit(self, node: ast.AST) -> str:
        children = []
        for child in ast.iter_child_nodes(node):
            children.append(self.visit(child))
        return f"{type(node).__name__}({children})"
    
    def get_hash_set(self) -> Set[str]:
        return set(self.hashes)
    
    def get_dominant_patterns(self) -> List[str]:
        """Get pattern types that appear multiple times."""
        return [p for p, count in self.patterns.items() if count >= 1]


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class FunctionAST:
    """Parsed function with AST analysis."""
    qualified_name: str
    name: str
    file_path: str
    line_start: int
    line_end: int
    loc: int
    hash_set: Set[str]
    patterns: List[str]
    decorators: List[str]
    parent_class: Optional[str]
    is_method: bool


@dataclass
class BoilerplateCluster:
    """A cluster of structurally similar functions."""
    functions: List[FunctionAST]
    avg_similarity: float
    dominant_patterns: List[str]
    has_shared_abstraction: bool
    abstraction_type: Optional[str]  # "base_class", "decorator", "mixin", etc.


# ============================================================================
# Similarity Calculation
# ============================================================================

def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def cluster_by_similarity(
    functions: List[FunctionAST],
    threshold: float = 0.70
) -> List[List[FunctionAST]]:
    """Cluster functions by AST similarity using single-linkage clustering.
    
    Args:
        functions: List of parsed functions
        threshold: Minimum Jaccard similarity (default 70%)
        
    Returns:
        List of clusters (each cluster is a list of similar functions)
    """
    if len(functions) < 2:
        return []
    
    # Build similarity matrix
    n = len(functions)
    similar_pairs: Dict[int, Set[int]] = defaultdict(set)
    
    for i in range(n):
        for j in range(i + 1, n):
            sim = jaccard_similarity(functions[i].hash_set, functions[j].hash_set)
            if sim >= threshold:
                similar_pairs[i].add(j)
                similar_pairs[j].add(i)
    
    # Single-linkage clustering via union-find
    parent = list(range(n))
    
    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x: int, y: int):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    for i, neighbors in similar_pairs.items():
        for j in neighbors:
            union(i, j)
    
    # Group by cluster
    clusters_map: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        clusters_map[find(i)].append(i)
    
    # Convert to function lists, filter by minimum size
    clusters = []
    for indices in clusters_map.values():
        if len(indices) >= 3:  # Minimum cluster size
            cluster = [functions[i] for i in indices]
            clusters.append(cluster)
    
    return clusters


# ============================================================================
# Main Detector
# ============================================================================

class AIBoilerplateDetector(CodeSmellDetector):
    """Detects excessive boilerplate code using AST clustering.
    
    Uses research-backed approach:
    1. Parse all functions to normalized AST
    2. Cluster by AST similarity (>70% Jaccard threshold)
    3. For each cluster with 3+ functions, check for shared abstraction
    4. Flag clusters without abstraction as boilerplate
    
    Key patterns detected:
    - Same try/except structure
    - Same validation logic  
    - Same API call patterns with minor variations
    - CRUD operations that could be genericized
    """
    
    # Thresholds
    DEFAULT_SIMILARITY_THRESHOLD = 0.70  # 70% AST similarity
    DEFAULT_MIN_CLUSTER_SIZE = 3
    DEFAULT_MIN_LOC = 5
    DEFAULT_MAX_FINDINGS = 50
    
    def __init__(
        self,
        graph_client: DatabaseClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize AI boilerplate detector.
        
        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration with:
                - repository_path: Path to repository root
                - similarity_threshold: Jaccard threshold (default: 0.70)
                - min_cluster_size: Minimum cluster size (default: 3)
                - min_loc: Minimum lines of code (default: 5)
                - max_findings: Maximum findings (default: 50)
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        
        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.similarity_threshold = config.get(
            "similarity_threshold", self.DEFAULT_SIMILARITY_THRESHOLD
        )
        self.min_cluster_size = config.get(
            "min_cluster_size", self.DEFAULT_MIN_CLUSTER_SIZE
        )
        self.min_loc = config.get("min_loc", self.DEFAULT_MIN_LOC)
        self.max_findings = config.get("max_findings", self.DEFAULT_MAX_FINDINGS)
    
    def detect(self) -> List[Finding]:
        """Detect boilerplate patterns using AST clustering.
        
        Returns:
            List of findings for boilerplate patterns
        """
        logger.info("Running AIBoilerplateDetector with AST clustering")
        
        # Fetch functions from graph
        raw_functions = self._fetch_functions()
        if not raw_functions:
            logger.info("No functions found in graph")
            return []
        
        logger.debug(f"Fetched {len(raw_functions)} functions from graph")
        
        # Parse to AST and compute hashes
        parsed_functions = self._parse_functions(raw_functions)
        if len(parsed_functions) < self.min_cluster_size:
            logger.info(f"Only {len(parsed_functions)} parseable functions, need at least {self.min_cluster_size}")
            return []
        
        logger.debug(f"Parsed {len(parsed_functions)} functions to AST")
        
        # Cluster by similarity
        clusters = cluster_by_similarity(parsed_functions, self.similarity_threshold)
        logger.debug(f"Found {len(clusters)} clusters with 3+ functions")
        
        # Analyze clusters for abstraction opportunities
        boilerplate_clusters = []
        for cluster_funcs in clusters:
            cluster = self._analyze_cluster(cluster_funcs)
            if not cluster.has_shared_abstraction:
                boilerplate_clusters.append(cluster)
        
        logger.info(f"Found {len(boilerplate_clusters)} boilerplate clusters without abstraction")
        
        # Create findings
        findings = []
        for cluster in boilerplate_clusters:
            finding = self._create_finding(cluster)
            findings.append(finding)
        
        return findings[:self.max_findings]
    
    def _fetch_functions(self) -> List[Dict[str, Any]]:
        """Fetch Python functions from the graph."""
        repo_filter = self._get_isolation_filter("f")
        
        query = f"""
        MATCH (f:Function)
        WHERE f.name IS NOT NULL 
          AND f.lineStart IS NOT NULL
          AND f.lineEnd IS NOT NULL
          AND f.filePath IS NOT NULL
          AND f.filePath ENDS WITH '.py'
          {repo_filter}
        OPTIONAL MATCH (f)<-[:CONTAINS]-(c:Class)
        RETURN f.qualifiedName AS qualified_name,
               f.name AS name,
               f.lineStart AS line_start,
               f.lineEnd AS line_end,
               f.decorators AS decorators,
               f.is_method AS is_method,
               c.qualifiedName AS parent_class,
               f.filePath AS file_path
        LIMIT 1000
        """
        
        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(),
            )
            return [r for r in results if r.get("file_path")]
        except Exception as e:
            logger.error(f"Error fetching functions: {e}")
            return []
    
    def _parse_functions(self, raw_functions: List[Dict]) -> List[FunctionAST]:
        """Parse functions to AST and compute hashes."""
        file_cache: Dict[str, str] = {}
        parsed: List[FunctionAST] = []
        
        for func in raw_functions:
            file_path = func.get("file_path")
            if not file_path:
                continue
            
            # Load file content
            if file_path not in file_cache:
                full_path = self.repository_path / file_path
                if not full_path.exists():
                    continue
                try:
                    file_cache[file_path] = full_path.read_text(encoding="utf-8")
                except Exception:
                    continue
            
            source = file_cache[file_path]
            line_start = func.get("line_start", 1)
            line_end = func.get("line_end", line_start + 10)
            
            # Extract function source
            lines = source.split("\n")
            start_idx = max(0, line_start - 1)
            end_idx = min(len(lines), line_end)
            func_source = "\n".join(lines[start_idx:end_idx])
            
            if not func_source.strip():
                continue
            
            # Parse to AST
            try:
                dedented = textwrap.dedent(func_source)
                tree = ast.parse(dedented)
            except SyntaxError:
                continue
            
            # Find the function node
            func_node = None
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_node = node
                    break
            
            if not func_node:
                continue
            
            # Compute hashes
            normalizer = ASTNormalizer()
            hasher = BoilerplateASTHasher(normalizer)
            try:
                hasher.visit(func_node)
            except Exception:
                continue
            
            hash_set = hasher.get_hash_set()
            if len(hash_set) < 3:  # Too small to cluster meaningfully
                continue
            
            # Extract decorators
            decorators = func.get("decorators", []) or []
            if isinstance(decorators, str):
                decorators = [decorators]
            
            parsed.append(FunctionAST(
                qualified_name=func.get("qualified_name", ""),
                name=func.get("name", ""),
                file_path=file_path,
                line_start=line_start,
                line_end=line_end,
                loc=func.get("loc", 0),
                hash_set=hash_set,
                patterns=hasher.get_dominant_patterns(),
                decorators=decorators,
                parent_class=func.get("parent_class"),
                is_method=func.get("is_method", False) or False,
            ))
        
        return parsed
    
    def _analyze_cluster(self, functions: List[FunctionAST]) -> BoilerplateCluster:
        """Analyze a cluster of similar functions.
        
        Checks if they share a common abstraction:
        - Same parent class (methods of same class)
        - Same decorators (decorator-based abstraction)
        - Same base class inheritance
        """
        # Calculate average similarity
        similarities = []
        for i, f1 in enumerate(functions):
            for f2 in functions[i + 1:]:
                sim = jaccard_similarity(f1.hash_set, f2.hash_set)
                similarities.append(sim)
        avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0
        
        # Collect dominant patterns
        all_patterns = []
        for f in functions:
            all_patterns.extend(f.patterns)
        pattern_counts = defaultdict(int)
        for p in all_patterns:
            pattern_counts[p] += 1
        dominant = [p for p, c in pattern_counts.items() if c >= len(functions) // 2]
        
        # Check for shared abstraction
        has_abstraction = False
        abstraction_type = None
        
        # Check 1: Same parent class
        parent_classes = {f.parent_class for f in functions if f.parent_class}
        if len(parent_classes) == 1:
            has_abstraction = True
            abstraction_type = "same_class"
        
        # Check 2: Shared decorators suggesting abstraction
        if not has_abstraction:
            shared_decorators = None
            for f in functions:
                dec_set = frozenset(f.decorators)
                if shared_decorators is None:
                    shared_decorators = dec_set
                else:
                    shared_decorators &= dec_set
            
            if shared_decorators:
                # Check if decorators indicate an existing pattern
                abstraction_decorators = {
                    "abstractmethod", "abc.abstractmethod",
                    "property", "staticmethod", "classmethod",
                    "route", "app.route", "api_view",
                }
                if shared_decorators & abstraction_decorators:
                    has_abstraction = True
                    abstraction_type = "decorator_pattern"
        
        # Check 3: Different files but same naming pattern (likely intentional)
        if not has_abstraction:
            files = {f.file_path for f in functions}
            if len(files) == 1:
                # All in same file - more likely to be boilerplate
                pass
            elif len(files) == len(functions):
                # Each in different file with similar structure - could be intentional
                # Check if names follow a pattern (e.g., test_*, handle_*)
                names = [f.name for f in functions]
                prefixes = [n.split("_")[0] if "_" in n else n[:3] for n in names]
                if len(set(prefixes)) == 1:
                    # Same prefix pattern - might be intentional convention
                    # Still flag it but with lower confidence
                    pass
        
        return BoilerplateCluster(
            functions=functions,
            avg_similarity=avg_similarity,
            dominant_patterns=dominant,
            has_shared_abstraction=has_abstraction,
            abstraction_type=abstraction_type,
        )
    
    def _create_finding(self, cluster: BoilerplateCluster) -> Finding:
        """Create a finding from a boilerplate cluster."""
        finding_id = str(uuid.uuid4())
        
        # Determine severity
        size = len(cluster.functions)
        if size >= 6 and cluster.avg_similarity >= 0.85:
            severity = Severity.HIGH
        elif size >= 4 or cluster.avg_similarity >= 0.80:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW
        
        # Build title
        pattern_str = ", ".join(cluster.dominant_patterns[:2]) if cluster.dominant_patterns else "similar structure"
        title = f"Boilerplate: {size} functions with {pattern_str} ({int(cluster.avg_similarity * 100)}% similar)"
        
        # Build description
        func_names = sorted([f.name for f in cluster.functions])
        if len(func_names) > 5:
            func_display = ", ".join(func_names[:5]) + f" ... and {len(func_names) - 5} more"
        else:
            func_display = ", ".join(func_names)
        
        files = sorted({f.file_path for f in cluster.functions})
        file_display = ", ".join(files[:3])
        if len(files) > 3:
            file_display += f" ... and {len(files) - 3} more files"
        
        description = (
            f"Found {size} functions with {int(cluster.avg_similarity * 100)}% AST similarity "
            f"that lack a shared abstraction.\n\n"
            f"**Functions:** {func_display}\n\n"
            f"**Files:** {file_display}\n\n"
        )
        
        if cluster.dominant_patterns:
            description += f"**Patterns detected:** {', '.join(cluster.dominant_patterns)}\n\n"
        
        description += (
            "These similar functions could be consolidated into a single parameterized "
            "function, decorator, or base class to reduce code duplication and improve "
            "maintainability."
        )
        
        # Generate suggestion based on patterns
        suggestion = self._generate_suggestion(cluster)
        
        # Calculate abstraction potential
        abstraction_potential = min(1.0, cluster.avg_similarity + (size / 10))
        
        finding = Finding(
            id=finding_id,
            detector="AIBoilerplateDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=[f.qualified_name for f in cluster.functions],
            affected_files=list(files),
            graph_context={
                "cluster_size": size,
                "avg_similarity": round(cluster.avg_similarity, 3),
                "dominant_patterns": cluster.dominant_patterns,
                "abstraction_potential": round(abstraction_potential, 2),
                "functions": [f.name for f in cluster.functions],
            },
            suggested_fix=suggestion,
            estimated_effort=self._estimate_effort(size),
            created_at=datetime.now(),
            why_it_matters=(
                "Repeated boilerplate code increases maintenance burden. "
                "When the pattern needs to change, you must update every copy. "
                "Abstracting common patterns reduces bugs and improves consistency."
            ),
        )
        
        # Add collaboration metadata
        evidence = [f"ast_similarity_{int(cluster.avg_similarity * 100)}pct"]
        evidence.extend([f"pattern_{p}" for p in cluster.dominant_patterns[:3]])
        
        confidence = 0.6 + (cluster.avg_similarity * 0.3) + (min(size, 10) / 50)
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="AIBoilerplateDetector",
            confidence=min(0.95, confidence),
            evidence=evidence,
            tags=["boilerplate", "ai_generated", "refactoring"] + cluster.dominant_patterns[:2],
        ))
        
        # Flag entities
        if self.enricher:
            for func in cluster.functions:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=func.qualified_name,
                        detector="AIBoilerplateDetector",
                        severity=severity.value,
                        issues=["boilerplate", "missing_abstraction"],
                        confidence=confidence,
                        metadata={
                            "cluster_size": size,
                            "avg_similarity": round(cluster.avg_similarity, 3),
                            "patterns": cluster.dominant_patterns,
                        }
                    )
                except Exception:
                    pass
        
        return finding
    
    def _generate_suggestion(self, cluster: BoilerplateCluster) -> str:
        """Generate abstraction suggestion based on detected patterns."""
        patterns = set(cluster.dominant_patterns)
        
        if "try_except" in patterns or "error_handling" in patterns:
            return (
                "**Suggested abstraction: Error handling decorator**\n\n"
                "```python\n"
                "def handle_errors(error_handler=None):\n"
                "    def decorator(func):\n"
                "        @wraps(func)\n"
                "        def wrapper(*args, **kwargs):\n"
                "            try:\n"
                "                return func(*args, **kwargs)\n"
                "            except Exception as e:\n"
                "                if error_handler:\n"
                "                    return error_handler(e)\n"
                "                raise\n"
                "        return wrapper\n"
                "    return decorator\n"
                "```\n\n"
                "Apply `@handle_errors()` to consolidate the try/except pattern."
            )
        
        if "validation" in patterns:
            return (
                "**Suggested abstraction: Validation decorator or helper**\n\n"
                "```python\n"
                "def validate(*validators):\n"
                "    def decorator(func):\n"
                "        @wraps(func)\n"
                "        def wrapper(*args, **kwargs):\n"
                "            for validator in validators:\n"
                "                validator(*args, **kwargs)\n"
                "            return func(*args, **kwargs)\n"
                "        return wrapper\n"
                "    return decorator\n"
                "```\n\n"
                "Or create reusable validation functions."
            )
        
        if "crud" in patterns or "http_method" in patterns:
            return (
                "**Suggested abstraction: Generic CRUD handler or base class**\n\n"
                "```python\n"
                "class BaseCRUDHandler:\n"
                "    model = None  # Override in subclass\n"
                "    \n"
                "    def create(self, data): ...\n"
                "    def read(self, id): ...\n"
                "    def update(self, id, data): ...\n"
                "    def delete(self, id): ...\n"
                "```\n\n"
                "Or use a factory function to generate endpoints."
            )
        
        if "database" in patterns:
            return (
                "**Suggested abstraction: Repository pattern or generic query helper**\n\n"
                "```python\n"
                "class BaseRepository:\n"
                "    model = None\n"
                "    \n"
                "    def get(self, **filters): ...\n"
                "    def create(self, **data): ...\n"
                "    def update(self, id, **data): ...\n"
                "```\n\n"
                "Consolidate database access patterns."
            )
        
        if "async" in patterns:
            return (
                "**Suggested abstraction: Async handler base or decorator**\n\n"
                "Create a base async handler or use a decorator to wrap common "
                "async patterns like connection management, retry logic, etc."
            )
        
        # Generic suggestion
        return (
            "**Suggested abstractions:**\n\n"
            "1. **Extract common logic** into a shared helper function\n"
            "2. **Create a decorator** if there's a wrapper pattern\n"
            "3. **Use a factory function** to generate variations\n"
            "4. **Create a base class** with template method pattern\n"
            "5. **Consolidate into single function** with parameters for variations"
        )
    
    def _estimate_effort(self, cluster_size: int) -> str:
        """Estimate refactoring effort."""
        if cluster_size >= 8:
            return "Large (1-2 days)"
        elif cluster_size >= 5:
            return "Medium (4-8 hours)"
        else:
            return "Small (2-4 hours)"
    
    def severity(self, finding: Finding) -> Severity:
        """Calculate severity from finding context."""
        return finding.severity
