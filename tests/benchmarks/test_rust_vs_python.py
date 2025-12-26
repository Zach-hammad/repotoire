"""Benchmark comparisons between Rust and Python implementations (REPO-167).

This module provides pytest-benchmark tests that compare the performance of:
1. Rust duplicate detection vs jscpd (Node.js)
2. Rust graph algorithms vs NetworkX
3. Rust pylint rules vs native Pylint

Run with: pytest tests/benchmarks/test_rust_vs_python.py --benchmark-only

To compare against baseline:
    pytest tests/benchmarks/test_rust_vs_python.py --benchmark-compare
"""

import os
import tempfile
from pathlib import Path
from typing import List, Tuple, Dict

import pytest

# Check if Rust extension is available
try:
    import repotoire_fast
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def python_source_files() -> List[Tuple[str, str]]:
    """Generate Python source files for duplicate detection benchmarks."""
    files = []

    # Template for generating files with some duplication
    base_code = '''
"""Module {module_num}."""

from typing import List, Dict, Optional
import json

class DataProcessor{module_num}:
    """Process data for module {module_num}."""

    def __init__(self, config: dict):
        self.config = config
        self._cache = {{}}

    def process(self, data: List[dict]) -> List[dict]:
        """Process a list of data items."""
        results = []
        for item in data:
            processed = self._process_item(item)
            if processed:
                results.append(processed)
        return results

    def _process_item(self, item: dict) -> Optional[dict]:
        """Process a single item with validation."""
        if not item:
            return None
        if 'id' not in item:
            return None
        if 'name' not in item:
            return None

        key = json.dumps(item, sort_keys=True)
        if key in self._cache:
            return self._cache[key]

        result = {{
            'id': item['id'],
            'name': item['name'].strip().upper(),
            'processed': True,
            'module': {module_num}
        }}
        self._cache[key] = result
        return result


def validate_input(data: Dict) -> bool:
    """Validate input data structure."""
    if not data:
        return False
    if 'id' not in data:
        return False
    if 'name' not in data:
        return False
    if not isinstance(data['id'], (int, str)):
        return False
    return True


def process_batch(items: List[Dict]) -> List[Dict]:
    """Process a batch of items."""
    results = []
    for item in items:
        if validate_input(item):
            result = {{
                'id': item['id'],
                'name': item['name'].strip(),
                'status': 'processed'
            }}
            results.append(result)
    return results
'''

    # Generate files with intentional duplication
    for i in range(100):
        source = base_code.format(module_num=i)
        files.append((f"src/module_{i}/processor.py", source))

    return files


@pytest.fixture
def graph_edges() -> Tuple[int, List[Tuple[int, int]]]:
    """Generate graph edges for algorithm benchmarks."""
    num_nodes = 1000
    edges = []

    # Create a scale-free like graph
    for i in range(1, num_nodes):
        # Each node connects to ~3 previous nodes
        for j in range(min(3, i)):
            target = (i * 7 + j * 13) % i  # Pseudo-random target
            edges.append((i, target))
            edges.append((target, i))  # Bidirectional

    return (num_nodes, edges)


@pytest.fixture
def large_graph_edges() -> Tuple[int, List[Tuple[int, int]]]:
    """Generate large graph for performance testing."""
    num_nodes = 5000
    edges = []

    for i in range(1, num_nodes):
        for j in range(min(4, i)):
            target = (i * 11 + j * 17) % i
            edges.append((i, target))
            edges.append((target, i))

    return (num_nodes, edges)


# ============================================================================
# DUPLICATE DETECTION BENCHMARKS
# ============================================================================

class TestDuplicateDetection:
    """Compare Rust vs Python/jscpd duplicate detection."""

    @pytest.mark.benchmark(group="duplicate_detection")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_duplicates_rust(self, benchmark, python_source_files):
        """Benchmark Rust duplicate detection."""
        from repotoire_fast import find_duplicates

        result = benchmark(
            find_duplicates,
            python_source_files,
            50,   # min_tokens
            5,    # min_lines
            0.0   # min_similarity
        )
        assert isinstance(result, list)

    @pytest.mark.benchmark(group="duplicate_detection")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_tokenization_rust(self, benchmark, python_source_files):
        """Benchmark Rust tokenization."""
        from repotoire_fast import tokenize_source

        # Combine all sources
        combined = "\n".join(src for _, src in python_source_files[:10])

        result = benchmark(tokenize_source, combined)
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.benchmark(group="duplicate_detection")
    def test_duplicates_python_fallback(self, benchmark, python_source_files):
        """Benchmark Python fallback duplicate detection."""
        from repotoire.detectors.duplicate_rust import _python_find_duplicates

        result = benchmark(
            _python_find_duplicates,
            python_source_files[:50],  # Use fewer files for Python
            50,   # min_tokens
            5     # min_lines
        )
        assert isinstance(result, list)


# ============================================================================
# GRAPH ALGORITHM BENCHMARKS
# ============================================================================

class TestGraphAlgorithms:
    """Compare Rust vs NetworkX graph algorithms."""

    @pytest.mark.benchmark(group="graph_algorithms")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_pagerank_rust(self, benchmark, graph_edges):
        """Benchmark Rust PageRank."""
        from repotoire_fast import graph_pagerank

        num_nodes, edges = graph_edges
        # Convert to u32 tuples
        edges_u32 = [(int(s), int(t)) for s, t in edges]

        result = benchmark(
            graph_pagerank,
            edges_u32,
            num_nodes,
            0.85,   # damping
            20,     # max_iterations
            1e-4    # tolerance
        )
        assert len(result) == num_nodes

    @pytest.mark.benchmark(group="graph_algorithms")
    def test_pagerank_networkx(self, benchmark, graph_edges):
        """Benchmark NetworkX PageRank for comparison."""
        import networkx as nx

        num_nodes, edges = graph_edges
        G = nx.DiGraph()
        G.add_nodes_from(range(num_nodes))
        G.add_edges_from(edges)

        result = benchmark(nx.pagerank, G, alpha=0.85, max_iter=20, tol=1e-4)
        assert len(result) == num_nodes

    @pytest.mark.benchmark(group="graph_algorithms")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_scc_rust(self, benchmark, graph_edges):
        """Benchmark Rust strongly connected components."""
        from repotoire_fast import graph_find_sccs

        num_nodes, edges = graph_edges
        edges_u32 = [(int(s), int(t)) for s, t in edges]

        result = benchmark(graph_find_sccs, edges_u32, num_nodes)
        assert isinstance(result, list)

    @pytest.mark.benchmark(group="graph_algorithms")
    def test_scc_networkx(self, benchmark, graph_edges):
        """Benchmark NetworkX SCC for comparison."""
        import networkx as nx

        num_nodes, edges = graph_edges
        G = nx.DiGraph()
        G.add_nodes_from(range(num_nodes))
        G.add_edges_from(edges)

        result = benchmark(lambda: list(nx.strongly_connected_components(G)))
        assert isinstance(result, list)

    @pytest.mark.benchmark(group="graph_algorithms")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_leiden_rust(self, benchmark, graph_edges):
        """Benchmark Rust Leiden community detection."""
        from repotoire_fast import graph_leiden

        num_nodes, edges = graph_edges
        edges_u32 = [(int(s), int(t)) for s, t in edges]

        result = benchmark(
            graph_leiden,
            edges_u32,
            num_nodes,
            1.0,   # resolution
            10     # max_iterations
        )
        assert len(result) == num_nodes

    @pytest.mark.benchmark(group="graph_algorithms")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_leiden_parallel_rust(self, benchmark, large_graph_edges):
        """Benchmark Rust parallel Leiden."""
        from repotoire_fast import graph_leiden_parallel

        num_nodes, edges = large_graph_edges
        edges_u32 = [(int(s), int(t)) for s, t in edges]

        result = benchmark(
            graph_leiden_parallel,
            edges_u32,
            num_nodes,
            1.0,   # resolution
            10,    # max_iterations
            True   # parallel
        )
        assert len(result) == num_nodes

    @pytest.mark.benchmark(group="graph_algorithms")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_betweenness_rust(self, benchmark, graph_edges):
        """Benchmark Rust betweenness centrality."""
        from repotoire_fast import graph_betweenness_centrality

        num_nodes, edges = graph_edges
        # Use smaller graph for betweenness (O(V*E) complexity)
        small_num = min(500, num_nodes)
        small_edges = [(s, t) for s, t in edges if s < small_num and t < small_num]

        result = benchmark(graph_betweenness_centrality, small_edges, small_num)
        assert len(result) == small_num

    @pytest.mark.benchmark(group="graph_algorithms")
    def test_betweenness_networkx(self, benchmark, graph_edges):
        """Benchmark NetworkX betweenness for comparison."""
        import networkx as nx

        num_nodes, edges = graph_edges
        # Use smaller graph
        small_num = min(500, num_nodes)
        small_edges = [(s, t) for s, t in edges if s < small_num and t < small_num]

        G = nx.DiGraph()
        G.add_nodes_from(range(small_num))
        G.add_edges_from(small_edges)

        result = benchmark(nx.betweenness_centrality, G)
        assert len(result) == small_num

    @pytest.mark.benchmark(group="graph_algorithms")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_harmonic_rust(self, benchmark, graph_edges):
        """Benchmark Rust harmonic centrality."""
        from repotoire_fast import graph_harmonic_centrality

        num_nodes, edges = graph_edges
        edges_u32 = [(int(s), int(t)) for s, t in edges]

        result = benchmark(
            graph_harmonic_centrality,
            edges_u32,
            num_nodes,
            True  # normalized
        )
        assert len(result) == num_nodes


# ============================================================================
# PYLINT RULES BENCHMARKS
# ============================================================================

class TestPylintRules:
    """Compare Rust pylint rules vs native Pylint."""

    @pytest.fixture
    def complex_python_source(self) -> str:
        """Generate complex Python source for pylint benchmarks."""
        return '''
"""Complex module for testing pylint rules."""

from typing import List, Dict, Optional, Any
import os
import sys
import json
from pathlib import Path


class GodClass:
    """A class with too many attributes and methods."""

    def __init__(self):
        self.attr1 = 1
        self.attr2 = 2
        self.attr3 = 3
        self.attr4 = 4
        self.attr5 = 5
        self.attr6 = 6
        self.attr7 = 7
        self.attr8 = 8
        self.attr9 = 9
        self.attr10 = 10

    def method1(self): pass
    def method2(self): pass
    def method3(self): pass


class DataClass:
    """A class with too few public methods."""

    def __init__(self, value):
        self.value = value


def complex_function(a, b, c, d, e, f, g, h):
    """Function with too many parameters."""
    result = a + b + c + d + e + f + g + h
    if result > 100:
        if result > 200:
            if result > 300:
                if result > 400:
                    return result * 2
                return result + 100
            return result
        return result // 2
    return result


def access_protected():
    """Function accessing protected members."""
    obj = GodClass()
    return obj._cache  # Protected access


for i in range(10):
    pass

print(i)  # Undefined loop variable


foo = 1
bar = 2
baz = 3
'''

    @pytest.mark.benchmark(group="pylint_rules")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_pylint_all_rules_rust(self, benchmark, complex_python_source):
        """Benchmark Rust all pylint rules."""
        from repotoire_fast import check_all_pylint_rules

        result = benchmark(
            check_all_pylint_rules,
            complex_python_source,
            "",     # module_path
            7,      # max_attributes
            2,      # min_public_methods
            1000,   # max_lines
            7,      # max_ancestors
            ["foo", "bar", "baz"]  # disallowed_names
        )
        assert isinstance(result, list)

    @pytest.mark.benchmark(group="pylint_rules")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_pylint_batch_rust(self, benchmark, python_source_files):
        """Benchmark Rust batch pylint analysis."""
        from repotoire_fast import check_all_pylint_rules_batch

        # Use first 50 files
        files = python_source_files[:50]

        result = benchmark(
            check_all_pylint_rules_batch,
            files,
            7,      # max_attributes
            2,      # min_public_methods
            1000,   # max_lines
            7,      # max_ancestors
            []      # disallowed_names
        )
        assert len(result) == len(files)


# ============================================================================
# FUNCTION BOUNDARY DETECTION BENCHMARKS (REPO-245)
# ============================================================================

def _python_extract_function_boundaries(source: str) -> list:
    """Python AST-based function boundary extraction (fallback implementation)."""
    import ast

    boundaries = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return boundaries

    def extract_from_node(node, prefix=""):
        """Recursively extract functions from AST node."""
        if isinstance(node, ast.ClassDef):
            class_prefix = f"{prefix}.{node.name}" if prefix else node.name
            for item in ast.iter_child_nodes(node):
                extract_from_node(item, class_prefix)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}.{node.name}" if prefix else node.name
            boundaries.append((name, node.lineno, node.end_lineno or node.lineno))
            # Recurse into function body for nested functions
            for item in ast.iter_child_nodes(node):
                extract_from_node(item, name)
        else:
            # Check other compound statements that might contain functions
            for item in ast.iter_child_nodes(node):
                extract_from_node(item, prefix)

    for item in ast.iter_child_nodes(tree):
        extract_from_node(item, "")

    return boundaries


class TestFunctionBoundaryDetection:
    """Compare Rust vs Python AST function boundary extraction (REPO-245)."""

    @pytest.fixture
    def function_rich_source(self) -> str:
        """Generate Python source with many functions for benchmarking."""
        code_parts = [
            '"""Module with many functions for benchmarking."""',
            'from typing import List, Dict, Optional',
            '',
        ]

        # Add top-level functions
        for i in range(20):
            code_parts.extend([
                f'def top_level_func_{i}(x: int, y: int) -> int:',
                f'    """Function {i}."""',
                f'    def nested_func_{i}(z):',
                f'        return z * 2',
                f'    return nested_func_{i}(x) + y',
                '',
            ])

        # Add classes with methods
        for i in range(10):
            code_parts.extend([
                f'class ServiceClass{i}:',
                f'    """Service class {i}."""',
                '',
                f'    def __init__(self, config: dict):',
                f'        self.config = config',
                f'        self._cache = {{}}',
                '',
                f'    def process(self, data: List[dict]) -> List[dict]:',
                f'        """Process data."""',
                f'        def validate_item(item):',
                f'            return item is not None',
                f'        return [d for d in data if validate_item(d)]',
                '',
                f'    async def async_process(self, data: List[dict]) -> List[dict]:',
                f'        """Async processing."""',
                f'        return await self._fetch(data)',
                '',
                f'    def _transform(self, item: dict) -> dict:',
                f'        return {{"transformed": item}}',
                '',
            ])

        return '\n'.join(code_parts)

    @pytest.fixture
    def function_rich_files(self, function_rich_source) -> list:
        """Generate multiple files for batch benchmarking."""
        files = []
        for i in range(50):
            files.append((f"src/module_{i}.py", function_rich_source))
        return files

    @pytest.mark.benchmark(group="function_boundaries")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_function_boundaries_rust_single(self, benchmark, function_rich_source):
        """Benchmark Rust function boundary detection (single file)."""
        from repotoire_fast import extract_function_boundaries

        result = benchmark(extract_function_boundaries, function_rich_source)
        assert isinstance(result, list)
        assert len(result) > 50  # Should find many functions

    @pytest.mark.benchmark(group="function_boundaries")
    def test_function_boundaries_python_single(self, benchmark, function_rich_source):
        """Benchmark Python AST function boundary detection (single file)."""
        result = benchmark(_python_extract_function_boundaries, function_rich_source)
        assert isinstance(result, list)
        assert len(result) > 50  # Should find many functions

    @pytest.mark.benchmark(group="function_boundaries")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_function_boundaries_rust_batch(self, benchmark, function_rich_files):
        """Benchmark Rust batch function boundary detection."""
        from repotoire_fast import extract_function_boundaries_batch

        result = benchmark(extract_function_boundaries_batch, function_rich_files)
        assert len(result) == len(function_rich_files)

    @pytest.mark.benchmark(group="function_boundaries")
    def test_function_boundaries_python_batch(self, benchmark, function_rich_files):
        """Benchmark Python AST batch function boundary detection."""
        def python_batch(files):
            return [
                (path, _python_extract_function_boundaries(source))
                for path, source in files
            ]

        result = benchmark(python_batch, function_rich_files)
        assert len(result) == len(function_rich_files)

    @pytest.mark.benchmark(group="function_boundaries")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_function_boundaries_correctness(self, function_rich_source):
        """Verify Rust and Python extract same number of functions."""
        from repotoire_fast import extract_function_boundaries

        rust_result = extract_function_boundaries(function_rich_source)
        python_result = _python_extract_function_boundaries(function_rich_source)

        # Both should find the same number of functions
        assert len(rust_result) == len(python_result), (
            f"Rust found {len(rust_result)} functions, "
            f"Python found {len(python_result)} functions"
        )


# ============================================================================
# BUG EXTRACTION BENCHMARKS (REPO-246)
# ============================================================================

def _python_extract_buggy_functions(repo_path: str, keywords: list, max_commits: int = 100):
    """Python GitPython-based bug extraction (fallback implementation)."""
    import ast
    import re
    from datetime import datetime
    from git import Repo

    repo = Repo(repo_path)
    buggy_functions = {}

    # Get commits
    commits = list(repo.iter_commits(max_count=max_commits))

    def is_bug_fix(message):
        msg_lower = message.lower()
        return any(kw in msg_lower for kw in keywords)

    def parse_functions(content, file_path):
        """Extract functions from Python source."""
        functions = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return functions

        module_name = file_path.replace("/", ".").removesuffix(".py")

        def extract(node, prefix=""):
            if isinstance(node, ast.ClassDef):
                class_prefix = f"{prefix}.{node.name}" if prefix else node.name
                for item in ast.iter_child_nodes(node):
                    extract(item, class_prefix)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{node.name}" if prefix else node.name
                functions.append((f"{module_name}.{name}", node.lineno, node.end_lineno or node.lineno))
                for item in ast.iter_child_nodes(node):
                    extract(item, name)
            else:
                for item in ast.iter_child_nodes(node):
                    extract(item, prefix)

        for item in ast.iter_child_nodes(tree):
            extract(item, "")
        return functions

    def extract_changed_lines(diff):
        """Extract changed line numbers from diff."""
        changed_lines = set()
        if not diff.diff:
            return changed_lines

        try:
            diff_text = diff.diff.decode("utf-8", errors="ignore")
        except (AttributeError, UnicodeDecodeError):
            return changed_lines

        hunk_pattern = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
        current_line = 0
        for line in diff_text.split("\n"):
            match = hunk_pattern.match(line)
            if match:
                current_line = int(match.group(2))
                continue
            if current_line > 0:
                if line.startswith("+") and not line.startswith("+++"):
                    changed_lines.add(current_line)
                    current_line += 1
                elif line.startswith("-") and not line.startswith("---"):
                    changed_lines.add(current_line)
                else:
                    current_line += 1
        return changed_lines

    for commit in commits:
        if not is_bug_fix(commit.message):
            continue
        if len(commit.parents) > 1:
            continue

        parent = commit.parents[0] if commit.parents else None
        if parent:
            diffs = parent.diff(commit, create_patch=True)
        else:
            diffs = commit.diff(None, create_patch=True)

        for diff in diffs:
            file_path = diff.b_path or diff.a_path
            if not file_path or not file_path.endswith(".py"):
                continue
            if "test" in file_path.lower() or "__pycache__" in file_path:
                continue

            changed_lines = extract_changed_lines(diff)
            if not changed_lines:
                continue

            try:
                if diff.b_blob:
                    content = diff.b_blob.data_stream.read().decode("utf-8", errors="ignore")
                else:
                    continue

                functions = parse_functions(content, file_path)
                for qname, start, end in functions:
                    if any(start <= line <= end for line in changed_lines):
                        if qname not in buggy_functions:
                            buggy_functions[qname] = {
                                "qualified_name": qname,
                                "file_path": file_path,
                                "commit_sha": commit.hexsha,
                                "commit_message": commit.message.strip()[:200],
                            }
            except Exception:
                continue

    return list(buggy_functions.values())


class TestBugExtraction:
    """Compare Rust vs Python bug extraction (REPO-246)."""

    @pytest.fixture
    def repo_path(self, tmp_path) -> str:
        """Create a synthetic git repository for benchmarking."""
        import subprocess

        repo_dir = tmp_path / "bench_repo"
        repo_dir.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_dir, check=True, capture_output=True
        )

        # Create files with many functions
        src_dir = repo_dir / "src"
        src_dir.mkdir()

        # Generate initial files
        for i in range(10):
            content = f'''"""Module {i}."""

class Service{i}:
    """Service class {i}."""

    def __init__(self, config):
        self.config = config

    def process(self, data):
        """Process data."""
        return [d for d in data if d]

    def validate(self, item):
        """Validate item."""
        return item is not None

    def transform(self, item):
        """Transform item."""
        return {{"transformed": item}}


def helper_{i}(x, y):
    """Helper function."""
    return x + y


def another_helper_{i}(a, b, c):
    """Another helper."""
    return a * b + c
'''
            (src_dir / f"module_{i}.py").write_text(content)

        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_dir, check=True, capture_output=True
        )

        # Create commits with bug fixes (simulate history)
        for commit_num in range(50):
            module_num = commit_num % 10
            content = f'''"""Module {module_num}."""

class Service{module_num}:
    """Service class {module_num}."""

    def __init__(self, config):
        self.config = config
        self._version = {commit_num}  # Fix: track version

    def process(self, data):
        """Process data."""
        # Fix: handle empty data v{commit_num}
        if not data:
            return []
        return [d for d in data if d]

    def validate(self, item):
        """Validate item."""
        # Bug fix: check None properly
        if item is None:
            return False
        return bool(item)

    def transform(self, item):
        """Transform item."""
        return {{"transformed": item, "v": {commit_num}}}


def helper_{module_num}(x, y):
    """Helper function."""
    # Fix: handle zero division
    if y == 0:
        return x
    return x + y


def another_helper_{module_num}(a, b, c):
    """Another helper."""
    return a * b + c + {commit_num}
'''
            (src_dir / f"module_{module_num}.py").write_text(content)
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)

            # Alternate between bug fix and regular commits
            if commit_num % 3 == 0:
                msg = f"Fix: handle edge case in module {module_num}"
            elif commit_num % 3 == 1:
                msg = f"Bug fix: improve validation in module {module_num}"
            else:
                msg = f"Refactor: update module {module_num}"

            subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=repo_dir, check=True, capture_output=True
            )

        return str(repo_dir)

    @pytest.mark.benchmark(group="bug_extraction")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_bug_extraction_rust(self, benchmark, repo_path):
        """Benchmark Rust parallel bug extraction."""
        from repotoire_fast import extract_buggy_functions_parallel

        result = benchmark(
            extract_buggy_functions_parallel,
            repo_path,
            ["fix", "bug", "error"],
            None,  # since_date
            100,   # max_commits
        )
        assert isinstance(result, list)

    @pytest.mark.benchmark(group="bug_extraction")
    def test_bug_extraction_python(self, benchmark, repo_path):
        """Benchmark Python GitPython bug extraction."""
        result = benchmark(
            _python_extract_buggy_functions,
            repo_path,
            ["fix", "bug", "error"],
            100,   # max_commits
        )
        assert isinstance(result, list)

    @pytest.mark.benchmark(group="bug_extraction")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_bug_extraction_rust_200_commits(self, benchmark, repo_path):
        """Benchmark Rust parallel bug extraction (200 commits)."""
        from repotoire_fast import extract_buggy_functions_parallel

        result = benchmark(
            extract_buggy_functions_parallel,
            repo_path,
            ["fix", "bug", "error"],
            None,  # since_date
            200,   # max_commits
        )
        assert isinstance(result, list)

    @pytest.mark.benchmark(group="bug_extraction")
    def test_bug_extraction_python_200_commits(self, benchmark, repo_path):
        """Benchmark Python GitPython bug extraction (200 commits)."""
        result = benchmark(
            _python_extract_buggy_functions,
            repo_path,
            ["fix", "bug", "error"],
            200,   # max_commits
        )
        assert isinstance(result, list)


# ============================================================================
# SUMMARY REPORT
# ============================================================================

class TestSummary:
    """Generate performance summary."""

    @pytest.mark.benchmark(group="summary")
    @pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")
    def test_rust_extension_info(self, benchmark):
        """Report Rust extension availability and version."""
        def get_info():
            import repotoire_fast
            return {
                "available": True,
                "module": repotoire_fast.__name__,
            }

        result = benchmark(get_info)
        assert result["available"]
