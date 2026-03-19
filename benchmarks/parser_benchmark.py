"""Benchmark: Actual PythonParser performance on real files.

This measures the real-world performance of the refactored parser.

Run with: uv run python benchmarks/parser_benchmark.py
"""

import time
import statistics
from pathlib import Path

from repotoire.parsers.python_parser import PythonParser


def benchmark_parser(file_path: Path, iterations: int = 10) -> tuple[float, int, int]:
    """Benchmark the parser on a single file.

    Returns: (avg_ms, entity_count, relationship_count)
    """
    parser = PythonParser()

    # Warm-up
    tree = parser.parse(str(file_path))
    entities = parser.extract_entities(tree, str(file_path))
    relationships = parser.extract_relationships(tree, str(file_path), entities)

    # Benchmark
    times = []
    for _ in range(iterations):
        parser = PythonParser()  # Fresh parser each time
        start = time.perf_counter()

        tree = parser.parse(str(file_path))
        entities = parser.extract_entities(tree, str(file_path))
        relationships = parser.extract_relationships(tree, str(file_path), entities)

        times.append((time.perf_counter() - start) * 1000)

    return statistics.mean(times), len(entities), len(relationships)


def find_python_files(repo_path: Path, max_files: int = 30) -> list[Path]:
    """Find Python files for benchmarking."""
    files = []
    for py_file in repo_path.rglob("*.py"):
        if any(part.startswith((".", "__")) for part in py_file.parts):
            continue
        if "test" in py_file.name.lower():
            continue
        try:
            py_file.read_text()
            files.append(py_file)
        except:
            continue
        if len(files) >= max_files:
            break
    return sorted(files, key=lambda f: f.stat().st_size, reverse=True)


def main():
    print("=" * 80)
    print("PythonParser Benchmark (Post-REPO-374 Refactor)")
    print("=" * 80)
    print()

    repo_path = Path(__file__).parent.parent / "repotoire"
    if not repo_path.exists():
        print(f"Error: Could not find repotoire source at {repo_path}")
        return

    files = find_python_files(repo_path, max_files=30)
    print(f"Found {len(files)} Python files in {repo_path}")
    print()

    print(f"{'File':<50} {'Size':>8} {'Time (ms)':>10} {'Entities':>10} {'Rels':>8}")
    print("-" * 90)

    total_time = 0
    total_entities = 0
    total_rels = 0
    total_size = 0

    for py_file in files:
        rel_path = py_file.relative_to(repo_path.parent)
        file_size = py_file.stat().st_size

        avg_ms, entities, rels = benchmark_parser(py_file, iterations=10)

        total_time += avg_ms
        total_entities += entities
        total_rels += rels
        total_size += file_size

        display_name = str(rel_path)
        if len(display_name) > 49:
            display_name = "..." + display_name[-46:]

        print(f"{display_name:<50} {file_size:>7,} {avg_ms:>10.2f} {entities:>10} {rels:>8}")

    print("-" * 90)
    print(f"{'TOTAL':<50} {total_size:>7,} {total_time:>10.2f} {total_entities:>10} {total_rels:>8}")
    print()

    print("Summary:")
    print(f"  Files processed: {len(files)}")
    print(f"  Total size: {total_size:,} bytes ({total_size/1024:.1f} KB)")
    print(f"  Total time: {total_time:.2f} ms")
    print(f"  Throughput: {total_size/1024/total_time*1000:.1f} KB/s")
    print(f"  Avg per file: {total_time/len(files):.2f} ms")
    print(f"  Total entities: {total_entities:,}")
    print(f"  Total relationships: {total_rels:,}")
    print()

    # Projections
    print("Projected time for full codebase parsing:")
    for file_count in [100, 500, 1000, 5000]:
        avg_ms = total_time / len(files)
        proj_s = avg_ms * file_count / 1000
        print(f"  {file_count:>5} files: {proj_s:.1f}s")


if __name__ == "__main__":
    main()
