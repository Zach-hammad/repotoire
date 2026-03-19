"""Benchmark: Single-pass AST visitor vs. multiple ast.walk() calls.

This compares the performance of:
1. OLD approach: Multiple ast.walk() calls (simulating pre-REPO-374 behavior)
2. NEW approach: Single-pass ast.NodeVisitor pattern

Run with: uv run python benchmarks/ast_traversal_benchmark.py
"""

import ast
import time
import statistics
from pathlib import Path
from typing import List, Dict, Any, Tuple


# =============================================================================
# OLD APPROACH: Multiple ast.walk() calls (simulating pre-refactor behavior)
# =============================================================================

def old_approach_extract_classes(tree: ast.AST) -> List[ast.ClassDef]:
    """Extract all classes - requires full tree walk."""
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def old_approach_extract_functions(tree: ast.AST) -> List[ast.FunctionDef]:
    """Extract all functions - requires full tree walk."""
    return [node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def old_approach_extract_imports(tree: ast.AST) -> List[ast.Import]:
    """Extract imports - requires full tree walk."""
    return [node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))]


def old_approach_find_decorators(tree: ast.AST) -> List[ast.expr]:
    """Find all decorators - requires full tree walk."""
    decorators = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            decorators.extend(node.decorator_list)
    return decorators


def old_approach_find_calls(tree: ast.AST) -> List[ast.Call]:
    """Find all function calls - requires full tree walk."""
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def old_approach_find_attributes(tree: ast.AST) -> List[ast.Attribute]:
    """Find all attribute accesses - requires full tree walk."""
    return [node for node in ast.walk(tree) if isinstance(node, ast.Attribute)]


def old_approach_find_returns(tree: ast.AST) -> List[ast.Return]:
    """Find all return statements - requires full tree walk."""
    return [node for node in ast.walk(tree) if isinstance(node, ast.Return)]


def old_approach_full_extraction(tree: ast.AST) -> Dict[str, Any]:
    """Simulate old approach: multiple separate ast.walk() calls."""
    return {
        "classes": old_approach_extract_classes(tree),
        "functions": old_approach_extract_functions(tree),
        "imports": old_approach_extract_imports(tree),
        "decorators": old_approach_find_decorators(tree),
        "calls": old_approach_find_calls(tree),
        "attributes": old_approach_find_attributes(tree),
        "returns": old_approach_find_returns(tree),
    }


# =============================================================================
# NEW APPROACH: Single-pass ast.NodeVisitor
# =============================================================================

class SinglePassVisitor(ast.NodeVisitor):
    """Single-pass visitor that extracts everything in one traversal."""

    def __init__(self):
        self.classes: List[ast.ClassDef] = []
        self.functions: List[ast.FunctionDef] = []
        self.imports: List[ast.Import] = []
        self.decorators: List[ast.expr] = []
        self.calls: List[ast.Call] = []
        self.attributes: List[ast.Attribute] = []
        self.returns: List[ast.Return] = []

    def visit_ClassDef(self, node: ast.ClassDef):
        self.classes.append(node)
        self.decorators.extend(node.decorator_list)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.functions.append(node)
        self.decorators.extend(node.decorator_list)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.functions.append(node)
        self.decorators.extend(node.decorator_list)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        self.imports.append(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        self.imports.append(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        self.calls.append(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        self.attributes.append(node)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        self.returns.append(node)
        self.generic_visit(node)


def new_approach_full_extraction(tree: ast.AST) -> Dict[str, Any]:
    """New approach: single-pass visitor extracts everything at once."""
    visitor = SinglePassVisitor()
    visitor.visit(tree)
    return {
        "classes": visitor.classes,
        "functions": visitor.functions,
        "imports": visitor.imports,
        "decorators": visitor.decorators,
        "calls": visitor.calls,
        "attributes": visitor.attributes,
        "returns": visitor.returns,
    }


# =============================================================================
# BENCHMARK RUNNER
# =============================================================================

def benchmark_file(file_path: Path, iterations: int = 10) -> Tuple[float, float, Dict[str, int]]:
    """Benchmark both approaches on a single file.

    Returns: (old_avg_ms, new_avg_ms, node_counts)
    """
    source = file_path.read_text()
    tree = ast.parse(source)

    # Warm-up
    old_approach_full_extraction(tree)
    new_approach_full_extraction(tree)

    # Benchmark OLD approach
    old_times = []
    for _ in range(iterations):
        start = time.perf_counter()
        old_result = old_approach_full_extraction(tree)
        old_times.append((time.perf_counter() - start) * 1000)

    # Benchmark NEW approach
    new_times = []
    for _ in range(iterations):
        start = time.perf_counter()
        new_result = new_approach_full_extraction(tree)
        new_times.append((time.perf_counter() - start) * 1000)

    # Verify results match
    assert len(old_result["classes"]) == len(new_result["classes"])
    assert len(old_result["functions"]) == len(new_result["functions"])
    assert len(old_result["imports"]) == len(new_result["imports"])

    node_counts = {
        "classes": len(old_result["classes"]),
        "functions": len(old_result["functions"]),
        "imports": len(old_result["imports"]),
        "calls": len(old_result["calls"]),
        "attributes": len(old_result["attributes"]),
        "returns": len(old_result["returns"]),
        "total_nodes": sum(1 for _ in ast.walk(tree)),
    }

    return statistics.mean(old_times), statistics.mean(new_times), node_counts


def find_python_files(repo_path: Path, max_files: int = 50) -> List[Path]:
    """Find Python files for benchmarking."""
    files = []
    for py_file in repo_path.rglob("*.py"):
        # Skip test files, __pycache__, etc.
        if any(part.startswith((".", "__")) for part in py_file.parts):
            continue
        if "test" in py_file.name.lower():
            continue
        try:
            # Verify it's valid Python
            ast.parse(py_file.read_text())
            files.append(py_file)
        except:
            continue
        if len(files) >= max_files:
            break
    return sorted(files, key=lambda f: f.stat().st_size, reverse=True)


def main():
    print("=" * 70)
    print("AST Traversal Benchmark: Single-pass Visitor vs. Multiple ast.walk()")
    print("=" * 70)
    print()

    # Find repo root
    repo_path = Path(__file__).parent.parent / "repotoire"
    if not repo_path.exists():
        print(f"Error: Could not find repotoire source at {repo_path}")
        return

    files = find_python_files(repo_path, max_files=30)
    print(f"Found {len(files)} Python files in {repo_path}")
    print()

    # Benchmark each file
    results = []
    total_old = 0
    total_new = 0
    total_nodes = 0

    print(f"{'File':<45} {'Nodes':>8} {'Old (ms)':>10} {'New (ms)':>10} {'Speedup':>10}")
    print("-" * 85)

    for py_file in files:
        rel_path = py_file.relative_to(repo_path.parent)
        old_ms, new_ms, counts = benchmark_file(py_file, iterations=20)
        speedup = old_ms / new_ms if new_ms > 0 else float('inf')

        results.append({
            "file": str(rel_path),
            "nodes": counts["total_nodes"],
            "old_ms": old_ms,
            "new_ms": new_ms,
            "speedup": speedup,
        })

        total_old += old_ms
        total_new += new_ms
        total_nodes += counts["total_nodes"]

        # Truncate long file names
        display_name = str(rel_path)
        if len(display_name) > 44:
            display_name = "..." + display_name[-41:]

        print(f"{display_name:<45} {counts['total_nodes']:>8} {old_ms:>10.3f} {new_ms:>10.3f} {speedup:>9.2f}x")

    # Summary
    print("-" * 85)
    avg_speedup = total_old / total_new if total_new > 0 else float('inf')
    print(f"{'TOTAL':<45} {total_nodes:>8} {total_old:>10.3f} {total_new:>10.3f} {avg_speedup:>9.2f}x")
    print()

    # Find best/worst
    best = max(results, key=lambda r: r["speedup"])
    worst = min(results, key=lambda r: r["speedup"])

    print("Summary:")
    print(f"  Total files benchmarked: {len(files)}")
    print(f"  Total AST nodes: {total_nodes:,}")
    print(f"  Average speedup: {avg_speedup:.2f}x")
    print(f"  Best speedup: {best['speedup']:.2f}x ({best['file']})")
    print(f"  Worst speedup: {worst['speedup']:.2f}x ({worst['file']})")
    print()

    # Extrapolate to full codebase
    print("Projected impact on large codebases:")
    for file_count in [100, 500, 1000, 5000]:
        avg_old = total_old / len(files)
        avg_new = total_new / len(files)
        proj_old = avg_old * file_count / 1000  # seconds
        proj_new = avg_new * file_count / 1000  # seconds
        print(f"  {file_count:>5} files: {proj_old:.1f}s -> {proj_new:.1f}s (saves {proj_old - proj_new:.1f}s)")


if __name__ == "__main__":
    main()
