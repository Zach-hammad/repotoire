"""Shared query cache for detector parallelization.

Prefetches common graph data once, enabling O(1) lookups instead of
repeated Kuzu queries across 42+ detectors.

Target: 9min → <2min analysis time.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FunctionData:
    """Cached function node data."""
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    complexity: int = 0
    loc: int = 0
    parameters: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None


@dataclass
class ClassData:
    """Cached class node data."""
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    complexity: int = 0
    loc: int = 0
    is_abstract: bool = False
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    method_count: int = 0


@dataclass
class FileData:
    """Cached file node data."""
    qualified_name: str
    file_path: str
    loc: int = 0
    language: str = "python"


class QueryCache:
    """Shared cache for common detector queries.
    
    Prefetches all graph data once at analysis start, then provides
    O(1) lookups for detectors running in parallel.
    
    Usage:
        cache = QueryCache(db)
        cache.prefetch()  # One-time cost
        
        # In detectors:
        for func in cache.functions.values():
            if func.complexity > 10:
                ...
    """
    
    def __init__(self, db, repo_id: Optional[str] = None):
        """Initialize cache.
        
        Args:
            db: Database client (KuzuClient or FalkorDBClient)
            repo_id: Optional repo ID for filtering
        """
        self.db = db
        self.repo_id = repo_id
        self.is_kuzu = type(db).__name__ == "KuzuClient"
        
        # Node caches (keyed by qualified_name)
        self.functions: Dict[str, FunctionData] = {}
        self.classes: Dict[str, ClassData] = {}
        self.files: Dict[str, FileData] = {}
        
        # Relationship caches
        self.calls: Dict[str, Set[str]] = {}  # caller -> set of callees
        self.called_by: Dict[str, Set[str]] = {}  # callee -> set of callers
        self.imports: Dict[str, Set[str]] = {}  # file -> set of imported modules
        self.inherits: Dict[str, Set[str]] = {}  # child -> set of parents
        self.inherited_by: Dict[str, Set[str]] = {}  # parent -> set of children
        self.contains: Dict[str, Set[str]] = {}  # class -> set of methods
        self.contained_by: Dict[str, str] = {}  # method -> class
        
        # Aggregates (computed after prefetch)
        self.total_functions: int = 0
        self.total_classes: int = 0
        self.total_files: int = 0
        self.total_loc: int = 0
        
        # Prefetch timing
        self.prefetch_time: float = 0.0
        self._prefetched: bool = False
    
    def prefetch(self) -> None:
        """Prefetch all common graph data.
        
        Call once before running detectors. Subsequent calls are no-ops.
        """
        if self._prefetched:
            return
        
        start = time.time()
        logger.info("QueryCache: Starting prefetch...")
        
        try:
            self._prefetch_functions()
            self._prefetch_classes()
            self._prefetch_files()
            self._prefetch_calls()
            self._prefetch_imports()
            self._prefetch_inheritance()
            self._prefetch_contains()
            self._compute_aggregates()
            
            self._prefetched = True
            self.prefetch_time = time.time() - start
            
            logger.info(
                f"QueryCache: Prefetch complete in {self.prefetch_time:.2f}s - "
                f"{self.total_functions} functions, {self.total_classes} classes, "
                f"{self.total_files} files, {len(self.calls)} call edges"
            )
        except Exception as e:
            logger.error(f"QueryCache: Prefetch failed: {e}")
            raise
    
    def _get_repo_filter(self, alias: str = "n") -> str:
        """Get WHERE clause for repo filtering."""
        if self.repo_id:
            return f"AND {alias}.repoId = $repo_id"
        return ""
    
    def _get_params(self) -> Dict[str, Any]:
        """Get query params with repo_id if set."""
        return {"repo_id": self.repo_id} if self.repo_id else {}
    
    def _prefetch_functions(self) -> None:
        """Prefetch all Function nodes."""
        query = """
        MATCH (n:Function)
        WHERE n.qualifiedName IS NOT NULL
        RETURN 
            n.qualifiedName AS name,
            n.filePath AS file_path,
            n.lineStart AS line_start,
            n.lineEnd AS line_end,
            n.complexity AS complexity,
            n.loc AS loc,
            n.parameters AS parameters,
            n.return_type AS return_type,
            n.is_async AS is_async,
            n.decorators AS decorators,
            n.docstring AS docstring
        """
        
        results = self.db.execute_query(query, self._get_params(), timeout=120.0)
        
        for r in results:
            name = r.get("name")
            if not name:
                continue
            
            self.functions[name] = FunctionData(
                qualified_name=name,
                file_path=r.get("file_path") or "",
                line_start=r.get("line_start") or 0,
                line_end=r.get("line_end") or 0,
                complexity=r.get("complexity") or 0,
                loc=r.get("loc") or 0,
                parameters=r.get("parameters") or [],
                return_type=r.get("return_type"),
                is_async=r.get("is_async") or False,
                decorators=r.get("decorators") or [],
                docstring=r.get("docstring"),
            )
        
        logger.debug(f"QueryCache: Prefetched {len(self.functions)} functions")
    
    def _prefetch_classes(self) -> None:
        """Prefetch all Class nodes."""
        query = """
        MATCH (n:Class)
        WHERE n.qualifiedName IS NOT NULL
        RETURN 
            n.qualifiedName AS name,
            n.filePath AS file_path,
            n.lineStart AS line_start,
            n.lineEnd AS line_end,
            n.complexity AS complexity,
            n.loc AS loc,
            n.is_abstract AS is_abstract,
            n.decorators AS decorators,
            n.docstring AS docstring
        """
        
        results = self.db.execute_query(query, self._get_params(), timeout=120.0)
        
        for r in results:
            name = r.get("name")
            if not name:
                continue
            
            self.classes[name] = ClassData(
                qualified_name=name,
                file_path=r.get("file_path") or "",
                line_start=r.get("line_start") or 0,
                line_end=r.get("line_end") or 0,
                complexity=r.get("complexity") or 0,
                loc=r.get("loc") or 0,
                is_abstract=r.get("is_abstract") or False,
                decorators=r.get("decorators") or [],
                docstring=r.get("docstring"),
            )
        
        logger.debug(f"QueryCache: Prefetched {len(self.classes)} classes")
    
    def _prefetch_files(self) -> None:
        """Prefetch all File nodes."""
        query = """
        MATCH (n:File)
        WHERE n.qualifiedName IS NOT NULL
        RETURN 
            n.qualifiedName AS name,
            n.filePath AS file_path,
            n.loc AS loc,
            n.language AS language
        """
        
        results = self.db.execute_query(query, self._get_params(), timeout=60.0)
        
        for r in results:
            name = r.get("name")
            if not name:
                continue
            
            self.files[name] = FileData(
                qualified_name=name,
                file_path=r.get("file_path") or name,
                loc=r.get("loc") or 0,
                language=r.get("language") or "python",
            )
        
        logger.debug(f"QueryCache: Prefetched {len(self.files)} files")
    
    def _prefetch_calls(self) -> None:
        """Prefetch all CALLS relationships."""
        query = """
        MATCH (a:Function)-[:CALLS]->(b:Function)
        WHERE a.qualifiedName IS NOT NULL AND b.qualifiedName IS NOT NULL
        RETURN a.qualifiedName AS caller, b.qualifiedName AS callee
        """
        
        results = self.db.execute_query(query, self._get_params(), timeout=120.0)
        
        for r in results:
            caller = r.get("caller")
            callee = r.get("callee")
            if not caller or not callee:
                continue
            
            if caller not in self.calls:
                self.calls[caller] = set()
            self.calls[caller].add(callee)
            
            if callee not in self.called_by:
                self.called_by[callee] = set()
            self.called_by[callee].add(caller)
        
        logger.debug(f"QueryCache: Prefetched {len(self.calls)} call sources")
    
    def _prefetch_imports(self) -> None:
        """Prefetch all IMPORTS relationships."""
        # Try File->Module first, fall back to other patterns
        query = """
        MATCH (a)-[:IMPORTS]->(b)
        WHERE a.qualifiedName IS NOT NULL AND b.qualifiedName IS NOT NULL
        RETURN a.qualifiedName AS importer, b.qualifiedName AS imported
        """
        
        results = self.db.execute_query(query, self._get_params(), timeout=120.0)
        
        for r in results:
            importer = r.get("importer")
            imported = r.get("imported")
            if not importer or not imported:
                continue
            
            if importer not in self.imports:
                self.imports[importer] = set()
            self.imports[importer].add(imported)
        
        logger.debug(f"QueryCache: Prefetched {len(self.imports)} import sources")
    
    def _prefetch_inheritance(self) -> None:
        """Prefetch all INHERITS relationships."""
        query = """
        MATCH (child:Class)-[:INHERITS]->(parent:Class)
        WHERE child.qualifiedName IS NOT NULL AND parent.qualifiedName IS NOT NULL
        RETURN child.qualifiedName AS child, parent.qualifiedName AS parent
        """
        
        results = self.db.execute_query(query, self._get_params(), timeout=60.0)
        
        for r in results:
            child = r.get("child")
            parent = r.get("parent")
            if not child or not parent:
                continue
            
            if child not in self.inherits:
                self.inherits[child] = set()
            self.inherits[child].add(parent)
            
            if parent not in self.inherited_by:
                self.inherited_by[parent] = set()
            self.inherited_by[parent].add(child)
        
        logger.debug(f"QueryCache: Prefetched {len(self.inherits)} inheritance edges")
    
    def _prefetch_contains(self) -> None:
        """Prefetch all CONTAINS relationships (Class -> Function)."""
        query = """
        MATCH (c:Class)-[:CONTAINS]->(f:Function)
        WHERE c.qualifiedName IS NOT NULL AND f.qualifiedName IS NOT NULL
        RETURN c.qualifiedName AS class_name, f.qualifiedName AS method_name
        """
        
        results = self.db.execute_query(query, self._get_params(), timeout=60.0)
        
        for r in results:
            class_name = r.get("class_name")
            method_name = r.get("method_name")
            if not class_name or not method_name:
                continue
            
            if class_name not in self.contains:
                self.contains[class_name] = set()
            self.contains[class_name].add(method_name)
            self.contained_by[method_name] = class_name
        
        # Update method counts on classes
        for class_name, methods in self.contains.items():
            if class_name in self.classes:
                self.classes[class_name].method_count = len(methods)
        
        logger.debug(f"QueryCache: Prefetched {len(self.contains)} class->method edges")
    
    def _compute_aggregates(self) -> None:
        """Compute aggregate statistics."""
        self.total_functions = len(self.functions)
        self.total_classes = len(self.classes)
        self.total_files = len(self.files)
        self.total_loc = sum(f.loc for f in self.files.values())
    
    # -------------------------------------------------------------------------
    # Query helpers for detectors
    # -------------------------------------------------------------------------
    
    def get_function(self, name: str) -> Optional[FunctionData]:
        """Get function by qualified name."""
        return self.functions.get(name)
    
    def get_class(self, name: str) -> Optional[ClassData]:
        """Get class by qualified name."""
        return self.classes.get(name)
    
    def get_callees(self, func_name: str) -> Set[str]:
        """Get functions called by the given function."""
        return self.calls.get(func_name, set())
    
    def get_callers(self, func_name: str) -> Set[str]:
        """Get functions that call the given function."""
        return self.called_by.get(func_name, set())
    
    def get_methods(self, class_name: str) -> Set[str]:
        """Get methods contained by the given class."""
        return self.contains.get(class_name, set())
    
    def get_parent_class(self, method_name: str) -> Optional[str]:
        """Get the class containing the given method."""
        return self.contained_by.get(method_name)
    
    def get_parents(self, class_name: str) -> Set[str]:
        """Get parent classes of the given class."""
        return self.inherits.get(class_name, set())
    
    def get_children(self, class_name: str) -> Set[str]:
        """Get child classes of the given class."""
        return self.inherited_by.get(class_name, set())
    
    def get_imports(self, file_name: str) -> Set[str]:
        """Get modules imported by the given file."""
        return self.imports.get(file_name, set())
    
    def get_high_complexity_functions(self, threshold: int = 10) -> List[FunctionData]:
        """Get functions with complexity above threshold."""
        return [f for f in self.functions.values() if f.complexity >= threshold]
    
    def get_god_classes(self, method_threshold: int = 20, loc_threshold: int = 500) -> List[ClassData]:
        """Get classes exceeding god class thresholds."""
        return [
            c for c in self.classes.values()
            if c.method_count >= method_threshold or c.loc >= loc_threshold
        ]
    
    def get_long_parameter_functions(self, threshold: int = 5) -> List[FunctionData]:
        """Get functions with too many parameters."""
        return [f for f in self.functions.values() if len(f.parameters) >= threshold]
    
    def get_hub_functions(self, in_threshold: int = 10, out_threshold: int = 10) -> List[Tuple[FunctionData, int, int]]:
        """Get hub functions (high in-degree and/or out-degree).
        
        Returns:
            List of (function, in_degree, out_degree) tuples
        """
        hubs = []
        for name, func in self.functions.items():
            in_degree = len(self.called_by.get(name, set()))
            out_degree = len(self.calls.get(name, set()))
            if in_degree >= in_threshold or out_degree >= out_threshold:
                hubs.append((func, in_degree, out_degree))
        return hubs
