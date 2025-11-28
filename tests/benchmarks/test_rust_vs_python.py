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
