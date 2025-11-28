#!/usr/bin/env python3
"""Check for benchmark regressions (REPO-167).

This script compares benchmark results against a baseline and fails
if any benchmark exceeds the specified threshold.

Usage:
    python scripts/check_benchmark_regression.py benchmark.json --threshold 1.5
    python scripts/check_benchmark_regression.py benchmark.json --baseline baseline.json

Exit codes:
    0: All benchmarks within threshold
    1: One or more benchmarks exceeded threshold
    2: Error reading benchmark files
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def load_benchmark_json(path: Path) -> Dict:
    """Load pytest-benchmark JSON output.

    Args:
        path: Path to benchmark.json file

    Returns:
        Parsed benchmark data

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is invalid JSON
    """
    with open(path) as f:
        return json.load(f)


def extract_benchmarks(data: Dict) -> Dict[str, Dict]:
    """Extract benchmark results into a flat dict.

    Args:
        data: pytest-benchmark JSON data

    Returns:
        Dict mapping benchmark name to stats
    """
    results = {}

    for benchmark in data.get("benchmarks", []):
        name = benchmark.get("name", "unknown")
        stats = benchmark.get("stats", {})
        results[name] = {
            "mean": stats.get("mean", 0),
            "min": stats.get("min", 0),
            "max": stats.get("max", 0),
            "stddev": stats.get("stddev", 0),
            "rounds": stats.get("rounds", 0),
            "group": benchmark.get("group", "default"),
        }

    return results


def compare_benchmarks(
    current: Dict[str, Dict],
    baseline: Dict[str, Dict],
    threshold: float,
) -> Tuple[List[str], List[str], List[str]]:
    """Compare current benchmarks against baseline.

    Args:
        current: Current benchmark results
        baseline: Baseline benchmark results
        threshold: Maximum allowed ratio (1.5 = 50% slower)

    Returns:
        Tuple of (regressions, improvements, new_benchmarks)
    """
    regressions = []
    improvements = []
    new_benchmarks = []

    for name, stats in current.items():
        if name not in baseline:
            new_benchmarks.append(name)
            continue

        current_mean = stats["mean"]
        baseline_mean = baseline[name]["mean"]

        if baseline_mean == 0:
            continue

        ratio = current_mean / baseline_mean

        if ratio > threshold:
            regressions.append(
                f"{name}: {current_mean:.6f}s vs {baseline_mean:.6f}s "
                f"({ratio:.2f}x slower)"
            )
        elif ratio < 1 / threshold:
            improvements.append(
                f"{name}: {current_mean:.6f}s vs {baseline_mean:.6f}s "
                f"({1/ratio:.2f}x faster)"
            )

    return regressions, improvements, new_benchmarks


def generate_report(
    current: Dict[str, Dict],
    baseline: Optional[Dict[str, Dict]],
    threshold: float,
) -> str:
    """Generate a human-readable benchmark report.

    Args:
        current: Current benchmark results
        baseline: Optional baseline results
        threshold: Regression threshold

    Returns:
        Formatted report string
    """
    lines = ["# Benchmark Report", ""]

    # Group benchmarks
    groups: Dict[str, List[Tuple[str, Dict]]] = {}
    for name, stats in current.items():
        group = stats.get("group", "default")
        if group not in groups:
            groups[group] = []
        groups[group].append((name, stats))

    for group, benchmarks in sorted(groups.items()):
        lines.append(f"## {group}")
        lines.append("")
        lines.append("| Benchmark | Mean | Min | Max | Rounds |")
        lines.append("|-----------|------|-----|-----|--------|")

        for name, stats in sorted(benchmarks):
            mean = f"{stats['mean']*1000:.3f}ms"
            min_val = f"{stats['min']*1000:.3f}ms"
            max_val = f"{stats['max']*1000:.3f}ms"
            rounds = stats['rounds']
            lines.append(f"| {name} | {mean} | {min_val} | {max_val} | {rounds} |")

        lines.append("")

    if baseline:
        regressions, improvements, new_benchmarks = compare_benchmarks(
            current, baseline, threshold
        )

        if regressions:
            lines.append("## Regressions")
            lines.append("")
            for r in regressions:
                lines.append(f"- {r}")
            lines.append("")

        if improvements:
            lines.append("## Improvements")
            lines.append("")
            for i in improvements:
                lines.append(f"- {i}")
            lines.append("")

        if new_benchmarks:
            lines.append("## New Benchmarks")
            lines.append("")
            for n in new_benchmarks:
                lines.append(f"- {n}")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Check benchmark results for regressions"
    )
    parser.add_argument(
        "benchmark_file",
        type=Path,
        help="Path to benchmark.json from pytest-benchmark"
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Path to baseline benchmark.json for comparison"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.5,
        help="Regression threshold (default: 1.5 = 50%% slower)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write report to file"
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit with code 1 if regressions found"
    )

    args = parser.parse_args()

    # Load current benchmarks
    try:
        current_data = load_benchmark_json(args.benchmark_file)
        current = extract_benchmarks(current_data)
    except FileNotFoundError:
        print(f"Error: Benchmark file not found: {args.benchmark_file}")
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {args.benchmark_file}: {e}")
        sys.exit(2)

    # Load baseline if provided
    baseline = None
    if args.baseline:
        try:
            baseline_data = load_benchmark_json(args.baseline)
            baseline = extract_benchmarks(baseline_data)
        except FileNotFoundError:
            print(f"Warning: Baseline file not found: {args.baseline}")
        except json.JSONDecodeError as e:
            print(f"Warning: Invalid JSON in baseline: {e}")

    # Generate report
    report = generate_report(current, baseline, args.threshold)

    if args.output:
        args.output.write_text(report)
        print(f"Report written to {args.output}")
    else:
        print(report)

    # Check for regressions
    if baseline and args.fail_on_regression:
        regressions, _, _ = compare_benchmarks(current, baseline, args.threshold)
        if regressions:
            print(f"\nFound {len(regressions)} regression(s)!")
            sys.exit(1)

    print("\nBenchmark check passed!")
    sys.exit(0)


if __name__ == "__main__":
    main()
