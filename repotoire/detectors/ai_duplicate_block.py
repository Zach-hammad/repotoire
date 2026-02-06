"""AI Duplicate Block Detector.

Detects near-identical code blocks that AI coding assistants tend to create
(copy-paste patterns). Uses AST-based similarity analysis per ICSE 2025 research.

AI assistants often generate repetitive code with minor variations like:
- Different variable names but same logic
- Same structure with different literals
- Copy-paste patterns with slight modifications

This detector uses tree-sitter AST parsing with normalized identifier hashing
and Jaccard similarity to find these near-duplicates.
"""

import ast
import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)

# Generic identifier patterns commonly produced by AI assistants
GENERIC_IDENTIFIERS = frozenset({
    "result", "res", "ret", "return_value", "rv",
    "temp", "tmp", "t",
    "data", "d",
    "value", "val", "v",
    "item", "items", "i",
    "obj", "object", "o",
    "x", "y", "z",
    "a", "b", "c",
    "arr", "array", "list", "lst",
    "dict", "dictionary", "map", "mapping",
    "str", "string", "s",
    "num", "number", "n",
    "count", "cnt",
    "index", "idx",
    "key", "k",
    "var", "variable",
    "input", "output", "out",
    "response", "resp",
    "request", "req",
    "config", "cfg",
    "args", "kwargs",
    "params", "parameters",
    "options", "opts",
    "settings",
    "handler", "callback", "cb",
    "func", "fn", "function",
    "elem", "element",
    "node", "n",
    "current", "curr", "cur",
    "previous", "prev",
    "next",
})


class ASTNormalizer:
    """Normalizes Python AST for similarity comparison.
    
    Replaces identifiers with type-based placeholders:
    - Variables → VAR_N
    - Functions → FUNC_N  
    - Classes → CLASS_N
    """
    
    def __init__(self):
        self.var_counter = 0
        self.func_counter = 0
        self.class_counter = 0
        self.name_map: Dict[str, str] = {}
        
    def normalize_name(self, name: str, ctx_type: str = "var") -> str:
        """Normalize an identifier to a type-based placeholder.
        
        Args:
            name: Original identifier name
            ctx_type: Type of identifier ("var", "func", "class")
            
        Returns:
            Normalized placeholder string
        """
        if name in self.name_map:
            return self.name_map[name]
        
        if ctx_type == "func":
            self.func_counter += 1
            placeholder = f"FUNC_{self.func_counter}"
        elif ctx_type == "class":
            self.class_counter += 1
            placeholder = f"CLASS_{self.class_counter}"
        else:
            self.var_counter += 1
            placeholder = f"VAR_{self.var_counter}"
        
        self.name_map[name] = placeholder
        return placeholder
    
    def reset(self):
        """Reset counters and name mappings for a new function."""
        self.var_counter = 0
        self.func_counter = 0
        self.class_counter = 0
        self.name_map.clear()


class PythonASTHasher(ast.NodeVisitor):
    """Visits Python AST and produces normalized hash strings for subtrees."""
    
    def __init__(self, normalizer: ASTNormalizer):
        self.normalizer = normalizer
        self.hashes: List[str] = []
        self.identifiers: List[str] = []  # Track all identifiers for generic name detection
        
    def visit_Name(self, node: ast.Name) -> str:
        """Normalize variable names."""
        self.identifiers.append(node.id)
        normalized = self.normalizer.normalize_name(node.id, "var")
        return f"Name({normalized})"
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> str:
        """Normalize function definitions."""
        self.identifiers.append(node.name)
        normalized_name = self.normalizer.normalize_name(node.name, "func")
        args = self._visit_arguments(node.args)
        body = [self.visit(stmt) for stmt in node.body if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, (ast.Constant, ast.Str))]  # Skip docstrings
        decorators = [self.visit(d) for d in node.decorator_list]
        result = f"FunctionDef({normalized_name},{args},{body},{decorators})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> str:
        """Normalize async function definitions."""
        self.identifiers.append(node.name)
        normalized_name = self.normalizer.normalize_name(node.name, "func")
        args = self._visit_arguments(node.args)
        body = [self.visit(stmt) for stmt in node.body if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, (ast.Constant, ast.Str))]
        decorators = [self.visit(d) for d in node.decorator_list]
        result = f"AsyncFunctionDef({normalized_name},{args},{body},{decorators})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_ClassDef(self, node: ast.ClassDef) -> str:
        """Normalize class definitions."""
        self.identifiers.append(node.name)
        normalized_name = self.normalizer.normalize_name(node.name, "class")
        bases = [self.visit(b) for b in node.bases]
        body = [self.visit(stmt) for stmt in node.body if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, (ast.Constant, ast.Str))]
        result = f"ClassDef({normalized_name},{bases},{body})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Call(self, node: ast.Call) -> str:
        """Normalize function calls."""
        func = self.visit(node.func)
        args = [self.visit(a) for a in node.args]
        keywords = [f"{kw.arg}={self.visit(kw.value)}" for kw in node.keywords]
        result = f"Call({func},{args},{keywords})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Attribute(self, node: ast.Attribute) -> str:
        """Normalize attribute access."""
        value = self.visit(node.value)
        # Keep attribute names as-is (often API names)
        return f"Attr({value}.{node.attr})"
    
    def visit_Subscript(self, node: ast.Subscript) -> str:
        """Normalize subscript operations."""
        value = self.visit(node.value)
        slice_val = self.visit(node.slice)
        result = f"Subscript({value}[{slice_val}])"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_BinOp(self, node: ast.BinOp) -> str:
        """Normalize binary operations."""
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = type(node.op).__name__
        result = f"BinOp({left}{op}{right})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Compare(self, node: ast.Compare) -> str:
        """Normalize comparisons."""
        left = self.visit(node.left)
        ops = [type(op).__name__ for op in node.ops]
        comparators = [self.visit(c) for c in node.comparators]
        result = f"Compare({left},{ops},{comparators})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_If(self, node: ast.If) -> str:
        """Normalize if statements."""
        test = self.visit(node.test)
        body = [self.visit(stmt) for stmt in node.body]
        orelse = [self.visit(stmt) for stmt in node.orelse]
        result = f"If({test},{body},{orelse})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_For(self, node: ast.For) -> str:
        """Normalize for loops."""
        target = self.visit(node.target)
        iter_val = self.visit(node.iter)
        body = [self.visit(stmt) for stmt in node.body]
        result = f"For({target},{iter_val},{body})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_While(self, node: ast.While) -> str:
        """Normalize while loops."""
        test = self.visit(node.test)
        body = [self.visit(stmt) for stmt in node.body]
        result = f"While({test},{body})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Return(self, node: ast.Return) -> str:
        """Normalize return statements."""
        value = self.visit(node.value) if node.value else "None"
        result = f"Return({value})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Assign(self, node: ast.Assign) -> str:
        """Normalize assignments."""
        targets = [self.visit(t) for t in node.targets]
        value = self.visit(node.value)
        result = f"Assign({targets}={value})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_AugAssign(self, node: ast.AugAssign) -> str:
        """Normalize augmented assignments."""
        target = self.visit(node.target)
        op = type(node.op).__name__
        value = self.visit(node.value)
        result = f"AugAssign({target}{op}={value})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Constant(self, node: ast.Constant) -> str:
        """Normalize constants - keep type but not value."""
        return f"Const({type(node.value).__name__})"
    
    def visit_List(self, node: ast.List) -> str:
        """Normalize list literals."""
        elts = [self.visit(e) for e in node.elts]
        return f"List({elts})"
    
    def visit_Dict(self, node: ast.Dict) -> str:
        """Normalize dict literals."""
        keys = [self.visit(k) if k else "None" for k in node.keys]
        values = [self.visit(v) for v in node.values]
        return f"Dict({keys},{values})"
    
    def visit_Tuple(self, node: ast.Tuple) -> str:
        """Normalize tuple literals."""
        elts = [self.visit(e) for e in node.elts]
        return f"Tuple({elts})"
    
    def visit_ListComp(self, node: ast.ListComp) -> str:
        """Normalize list comprehensions."""
        elt = self.visit(node.elt)
        generators = [self._visit_comprehension(g) for g in node.generators]
        result = f"ListComp({elt},{generators})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_DictComp(self, node: ast.DictComp) -> str:
        """Normalize dict comprehensions."""
        key = self.visit(node.key)
        value = self.visit(node.value)
        generators = [self._visit_comprehension(g) for g in node.generators]
        result = f"DictComp({key}:{value},{generators})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Try(self, node: ast.Try) -> str:
        """Normalize try/except blocks."""
        body = [self.visit(stmt) for stmt in node.body]
        handlers = [self._visit_handler(h) for h in node.handlers]
        orelse = [self.visit(stmt) for stmt in node.orelse]
        finalbody = [self.visit(stmt) for stmt in node.finalbody]
        result = f"Try({body},{handlers},{orelse},{finalbody})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_With(self, node: ast.With) -> str:
        """Normalize with statements."""
        items = [f"{self.visit(item.context_expr)}as{self.visit(item.optional_vars) if item.optional_vars else 'None'}" 
                 for item in node.items]
        body = [self.visit(stmt) for stmt in node.body]
        result = f"With({items},{body})"
        self.hashes.append(hashlib.md5(result.encode()).hexdigest()[:8])
        return result
    
    def visit_Raise(self, node: ast.Raise) -> str:
        """Normalize raise statements."""
        exc = self.visit(node.exc) if node.exc else "None"
        return f"Raise({exc})"
    
    def visit_Assert(self, node: ast.Assert) -> str:
        """Normalize assert statements."""
        test = self.visit(node.test)
        return f"Assert({test})"
    
    def visit_Expr(self, node: ast.Expr) -> str:
        """Normalize expression statements."""
        return self.visit(node.value)
    
    def visit_Pass(self, node: ast.Pass) -> str:
        return "Pass"
    
    def visit_Break(self, node: ast.Break) -> str:
        return "Break"
    
    def visit_Continue(self, node: ast.Continue) -> str:
        return "Continue"
    
    def _visit_arguments(self, args: ast.arguments) -> str:
        """Normalize function arguments."""
        all_args = []
        for arg in args.posonlyargs + args.args:
            self.identifiers.append(arg.arg)
            all_args.append(self.normalizer.normalize_name(arg.arg, "var"))
        if args.vararg:
            self.identifiers.append(args.vararg.arg)
            all_args.append(f"*{self.normalizer.normalize_name(args.vararg.arg, 'var')}")
        for arg in args.kwonlyargs:
            self.identifiers.append(arg.arg)
            all_args.append(self.normalizer.normalize_name(arg.arg, "var"))
        if args.kwarg:
            self.identifiers.append(args.kwarg.arg)
            all_args.append(f"**{self.normalizer.normalize_name(args.kwarg.arg, 'var')}")
        return f"Args({all_args})"
    
    def _visit_comprehension(self, comp: ast.comprehension) -> str:
        """Normalize comprehension generators."""
        target = self.visit(comp.target)
        iter_val = self.visit(comp.iter)
        ifs = [self.visit(if_clause) for if_clause in comp.ifs]
        return f"Gen({target},{iter_val},{ifs})"
    
    def _visit_handler(self, handler: ast.ExceptHandler) -> str:
        """Normalize exception handlers."""
        exc_type = handler.type.id if handler.type and isinstance(handler.type, ast.Name) else "Exception"
        if handler.name:
            self.identifiers.append(handler.name)
            name = self.normalizer.normalize_name(handler.name, "var")
        else:
            name = "None"
        body = [self.visit(stmt) for stmt in handler.body]
        return f"Handler({exc_type},{name},{body})"
    
    def generic_visit(self, node: ast.AST) -> str:
        """Fallback for unhandled node types."""
        children = []
        for child in ast.iter_child_nodes(node):
            children.append(self.visit(child))
        return f"{type(node).__name__}({children})"
    
    def get_hash_set(self) -> Set[str]:
        """Get set of all subtree hashes."""
        return set(self.hashes)
    
    def get_generic_name_ratio(self) -> float:
        """Calculate ratio of generic identifiers."""
        if not self.identifiers:
            return 0.0
        generic_count = sum(1 for name in self.identifiers if name.lower() in GENERIC_IDENTIFIERS)
        return generic_count / len(self.identifiers)


def extract_function_source(source: str, line_start: int, line_end: int) -> str:
    """Extract function source code from file content.
    
    Args:
        source: Full file source code
        line_start: Starting line (1-indexed)
        line_end: Ending line (1-indexed)
        
    Returns:
        Function source code
    """
    lines = source.split('\n')
    # Convert to 0-indexed
    start_idx = max(0, line_start - 1)
    end_idx = min(len(lines), line_end)
    return '\n'.join(lines[start_idx:end_idx])


def parse_function_ast(source: str) -> Optional[ast.AST]:
    """Parse function source code to AST.
    
    Args:
        source: Function source code
        
    Returns:
        Parsed AST or None if parsing fails
    """
    # Dedent the source to handle indented methods
    import textwrap
    dedented = textwrap.dedent(source)
    
    try:
        tree = ast.parse(dedented)
        # Return the first function/method definition
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return tree
    except SyntaxError:
        return None


def compute_ast_hashes(func_ast: ast.AST) -> Tuple[Set[str], float, List[str]]:
    """Compute normalized AST hashes for a function.
    
    Args:
        func_ast: Parsed function AST
        
    Returns:
        Tuple of (hash set, generic name ratio, identifiers list)
    """
    normalizer = ASTNormalizer()
    hasher = PythonASTHasher(normalizer)
    hasher.visit(func_ast)
    return hasher.get_hash_set(), hasher.get_generic_name_ratio(), hasher.identifiers


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """Calculate Jaccard similarity between two sets.
    
    Args:
        set1: First set
        set2: Second set
        
    Returns:
        Jaccard similarity (0.0 to 1.0)
    """
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


class AIDuplicateBlockDetector(CodeSmellDetector):
    """Detect near-identical code blocks typical of AI-generated code.
    
    Uses AST-based similarity analysis per ICSE 2025 research:
    1. Parse functions to AST
    2. Normalize AST (replace identifiers with TYPE_N placeholders)
    3. Hash normalized AST subtrees
    4. Calculate Jaccard similarity of hash sets
    5. Threshold: >70% = duplicate
    
    Also detects generic naming patterns common in AI code.
    """

    # Default thresholds (based on ICSE 2025 research)
    DEFAULT_SIMILARITY_THRESHOLD = 0.70  # 70% Jaccard similarity
    DEFAULT_GENERIC_NAME_THRESHOLD = 0.40  # 40% generic identifiers
    DEFAULT_MIN_LOC = 5  # Minimum lines of code
    DEFAULT_MAX_FINDINGS = 50

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize AI duplicate block detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dict with:
                - repository_path: Path to repository root (required for AST parsing)
                - similarity_threshold: Jaccard similarity threshold (default: 0.70)
                - generic_name_threshold: Generic identifier ratio threshold (default: 0.40)
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
        self.generic_name_threshold = config.get(
            "generic_name_threshold", self.DEFAULT_GENERIC_NAME_THRESHOLD
        )
        self.min_loc = config.get("min_loc", self.DEFAULT_MIN_LOC)
        self.max_findings = config.get("max_findings", self.DEFAULT_MAX_FINDINGS)

    def detect(self) -> List[Finding]:
        """Detect near-duplicate code blocks using AST similarity.

        Returns:
            List of findings for AI-generated duplicate patterns
        """
        # Fetch all Python functions from the graph
        try:
            functions = self._fetch_functions()
        except Exception as e:
            logger.warning(f"AIDuplicateBlockDetector: Failed to fetch functions: {e}")
            return []
        
        if not functions:
            logger.info(
                "AIDuplicateBlockDetector: No Python functions found in graph "
                "(graph may be empty or missing filePath/loc properties)"
            )
            return []

        logger.debug(f"AIDuplicateBlockDetector: Processing {len(functions)} functions")

        # Parse and hash function ASTs
        func_data = self._process_functions(functions)
        
        if len(func_data) < 2:
            logger.info(
                f"AIDuplicateBlockDetector: Not enough parseable functions "
                f"(found {len(func_data)}, need at least 2)"
            )
            return []

        # Find near-duplicates using Jaccard similarity
        duplicates = self._find_duplicates(func_data)
        
        if not duplicates:
            logger.info(
                f"AIDuplicateBlockDetector: No duplicates found above "
                f"{self.similarity_threshold:.0%} threshold in {len(func_data)} functions"
            )
            return []
        
        # Create findings
        findings = self._create_findings(duplicates, func_data)
        
        logger.info(
            f"AIDuplicateBlockDetector found {len(findings)} near-duplicate pairs"
        )
        return findings[:self.max_findings]

    def _fetch_functions(self) -> List[Dict[str, Any]]:
        """Fetch Python functions from the graph.

        Returns:
            List of function data dictionaries
        """
        repo_filter = self._get_isolation_filter("f")

        # Try to get filePath directly from Function node (simpler, works without edges)
        # This handles graphs that don't have CONTAINS relationships
        query = f"""
        MATCH (f:Function)
        WHERE f.name IS NOT NULL 
          AND f.loc IS NOT NULL 
          AND f.loc >= $min_loc
          AND f.filePath IS NOT NULL
          AND f.filePath ENDS WITH '.py'
          {repo_filter}
        RETURN f.qualifiedName AS qualified_name,
               f.name AS name,
               f.lineStart AS line_start,
               f.lineEnd AS line_end,
               f.loc AS loc,
               f.filePath AS file_path
        ORDER BY f.loc DESC
        LIMIT 500
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(min_loc=self.min_loc),
            )
            functions = [r for r in results if r.get("file_path")]
            
            if functions:
                return functions
            
            # Fallback: try with CONTAINS edge if direct filePath didn't work
            logger.debug("No functions with direct filePath, trying CONTAINS edge lookup")
            fallback_query = f"""
            MATCH (f:Function)
            WHERE f.name IS NOT NULL 
              AND f.loc IS NOT NULL 
              AND f.loc >= $min_loc
              {repo_filter}
            OPTIONAL MATCH (f)<-[:CONTAINS*]-(file:File)
            WHERE file.language = 'python' OR file.filePath ENDS WITH '.py'
            RETURN f.qualifiedName AS qualified_name,
                   f.name AS name,
                   f.lineStart AS line_start,
                   f.lineEnd AS line_end,
                   f.loc AS loc,
                   file.filePath AS file_path
            ORDER BY f.loc DESC
            LIMIT 500
            """
            
            fallback_results = self.db.execute_query(
                fallback_query,
                self._get_query_params(min_loc=self.min_loc),
            )
            return [r for r in fallback_results if r.get("file_path")]
            
        except Exception as e:
            logger.error(f"Error fetching functions: {e}")
            return []

    def _process_functions(
        self, functions: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Parse functions and compute AST hashes.
        
        Args:
            functions: List of function data from graph
            
        Returns:
            Dict mapping qualified_name to processed data
        """
        # Cache file contents to avoid re-reading
        file_cache: Dict[str, str] = {}
        func_data: Dict[str, Dict[str, Any]] = {}
        
        for func in functions:
            file_path = func.get("file_path")
            if not file_path:
                continue
            
            # Get file content
            if file_path not in file_cache:
                full_path = self.repository_path / file_path
                if not full_path.exists():
                    continue
                try:
                    file_cache[file_path] = full_path.read_text(encoding='utf-8')
                except Exception:
                    continue
            
            source = file_cache[file_path]
            line_start = func.get("line_start", 1)
            line_end = func.get("line_end", line_start + 10)
            
            # Extract function source
            func_source = extract_function_source(source, line_start, line_end)
            if not func_source.strip():
                continue
            
            # Parse to AST
            func_ast = parse_function_ast(func_source)
            if func_ast is None:
                continue
            
            # Compute hashes
            try:
                hash_set, generic_ratio, identifiers = compute_ast_hashes(func_ast)
            except Exception:
                continue
            
            if not hash_set:
                continue
            
            qn = func.get("qualified_name", "")
            func_data[qn] = {
                **func,
                "hash_set": hash_set,
                "generic_ratio": generic_ratio,
                "identifiers": identifiers,
                "ast_size": len(hash_set),
            }
        
        return func_data

    def _find_duplicates(
        self, func_data: Dict[str, Dict[str, Any]]
    ) -> List[Tuple[str, str, float]]:
        """Find duplicate pairs using Jaccard similarity.
        
        Args:
            func_data: Processed function data
            
        Returns:
            List of (qn1, qn2, similarity) tuples
        """
        duplicates = []
        qnames = list(func_data.keys())
        seen_pairs: Set[Tuple[str, str]] = set()
        
        # Compare all pairs (O(n²) but filtered by AST size)
        for i, qn1 in enumerate(qnames):
            data1 = func_data[qn1]
            hash_set1 = data1["hash_set"]
            file1 = data1.get("file_path", "")
            size1 = data1["ast_size"]
            
            for qn2 in qnames[i + 1:]:
                data2 = func_data[qn2]
                file2 = data2.get("file_path", "")
                
                # Skip same-file comparisons
                if file1 == file2:
                    continue
                
                # Skip if AST sizes are too different (optimization)
                size2 = data2["ast_size"]
                if size1 > 0 and size2 > 0:
                    size_ratio = min(size1, size2) / max(size1, size2)
                    if size_ratio < 0.5:  # Skip if one is more than 2x larger
                        continue
                
                pair_key = tuple(sorted([qn1, qn2]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                
                # Calculate Jaccard similarity
                hash_set2 = data2["hash_set"]
                similarity = jaccard_similarity(hash_set1, hash_set2)
                
                if similarity >= self.similarity_threshold:
                    duplicates.append((qn1, qn2, similarity))
        
        # Sort by similarity (highest first)
        duplicates.sort(key=lambda x: x[2], reverse=True)
        return duplicates

    def _create_findings(
        self,
        duplicates: List[Tuple[str, str, float]],
        func_data: Dict[str, Dict[str, Any]],
    ) -> List[Finding]:
        """Create findings from duplicate pairs.
        
        Args:
            duplicates: List of (qn1, qn2, similarity) tuples
            func_data: Processed function data
            
        Returns:
            List of Finding objects
        """
        findings = []
        
        for qn1, qn2, similarity in duplicates:
            data1 = func_data.get(qn1, {})
            data2 = func_data.get(qn2, {})
            
            name1 = data1.get("name", "unknown")
            name2 = data2.get("name", "unknown")
            file1 = data1.get("file_path", "unknown")
            file2 = data2.get("file_path", "unknown")
            loc1 = data1.get("loc", 0)
            loc2 = data2.get("loc", 0)
            generic_ratio1 = data1.get("generic_ratio", 0)
            generic_ratio2 = data2.get("generic_ratio", 0)
            
            similarity_pct = int(similarity * 100)
            
            # Check for generic naming pattern
            has_generic_naming = (
                generic_ratio1 >= self.generic_name_threshold or
                generic_ratio2 >= self.generic_name_threshold
            )
            
            # Build description
            description = (
                f"Functions '{name1}' and '{name2}' have {similarity_pct}% AST similarity, "
                f"indicating AI-generated copy-paste patterns.\n\n"
                f"**{name1}** ({loc1} LOC): `{file1}`\n"
                f"**{name2}** ({loc2} LOC): `{file2}`\n\n"
            )
            
            if has_generic_naming:
                avg_generic = (generic_ratio1 + generic_ratio2) / 2
                description += (
                    f"⚠️ **Generic naming detected**: {int(avg_generic * 100)}% of identifiers "
                    f"use generic names (result, temp, data, etc.), a common AI pattern.\n\n"
                )
            
            description += (
                "Near-identical functions increase maintenance burden and "
                "can lead to inconsistent bug fixes."
            )
            
            suggestion = (
                "Consider one of the following approaches:\n"
                "1. **Extract common logic** into a shared helper function\n"
                "2. **Use a template/factory pattern** if variations are intentional\n"
                "3. **Consolidate** into a single implementation if truly duplicates\n"
                "4. **Add documentation** explaining why similar implementations exist"
            )
            
            # Determine severity based on similarity and generic naming
            if similarity >= 0.90 and has_generic_naming:
                severity = Severity.CRITICAL
            elif similarity >= 0.85 or (similarity >= 0.70 and has_generic_naming):
                severity = Severity.HIGH
            else:
                severity = Severity.MEDIUM
            
            finding = Finding(
                id=f"ai_duplicate_block_{qn1}_{qn2}",
                detector="AIDuplicateBlockDetector",
                severity=severity,
                title=f"AI-style duplicate: {name1} ≈ {name2} ({similarity_pct}% AST)",
                description=description,
                affected_nodes=[qn1, qn2],
                affected_files=[f for f in [file1, file2] if f != "unknown"],
                line_start=data1.get("line_start"),
                line_end=data1.get("line_end"),
                suggested_fix=suggestion,
                estimated_effort="Medium (1-2 hours)",
                graph_context={
                    "ast_similarity": round(similarity, 3),
                    "func1_loc": loc1,
                    "func2_loc": loc2,
                    "func1_generic_ratio": round(generic_ratio1, 3),
                    "func2_generic_ratio": round(generic_ratio2, 3),
                    "has_generic_naming": has_generic_naming,
                },
            )
            
            # Add collaboration metadata
            evidence = ["high_ast_similarity", "cross_file_duplicate"]
            if similarity >= 0.90:
                evidence.append("near_identical_ast")
            if has_generic_naming:
                evidence.append("generic_naming_pattern")
            
            confidence = min(0.6 + similarity * 0.4, 0.95)
            
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="AIDuplicateBlockDetector",
                confidence=confidence,
                evidence=evidence,
                tags=["ai_duplicate", "copy_paste", "duplication", "ast_similarity"],
            ))
            
            # Flag entities in graph
            if self.enricher:
                for qn in [qn1, qn2]:
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=qn,
                            detector="AIDuplicateBlockDetector",
                            severity=finding.severity.value,
                            issues=["ai_generated_duplicate"],
                            confidence=similarity,
                            metadata={
                                "duplicate_of": qn2 if qn == qn1 else qn1,
                                "ast_similarity": round(similarity, 3),
                            },
                        )
                    except Exception:
                        pass
            
            findings.append(finding)
        
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on similarity and generic naming.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        return finding.severity
