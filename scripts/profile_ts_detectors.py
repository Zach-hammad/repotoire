#!/usr/bin/env python3
"""Profile TypeScript/JavaScript detectors for performance analysis.

Usage:
    python scripts/profile_ts_detectors.py /path/to/ts-repo

This script measures:
- Execution time for each detector
- Number of findings
- Memory usage (if available)
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


def create_mock_graph_client():
    """Create a mock graph client for profiling."""
    client = MagicMock()
    client.execute_query.return_value = []
    return client


def profile_detector(detector_class, name: str, repo_path: Path, config: Dict = None) -> Dict[str, Any]:
    """Profile a single detector.

    Args:
        detector_class: Detector class to instantiate
        name: Human-readable detector name
        repo_path: Path to repository to analyze
        config: Optional detector configuration

    Returns:
        Dict with timing and finding information
    """
    print(f"\n{'='*60}")
    print(f"Profiling: {name}")
    print(f"{'='*60}")

    mock_client = create_mock_graph_client()
    detector_config = {"repository_path": str(repo_path)}
    if config:
        detector_config.update(config)

    try:
        # Instantiate detector
        start_init = time.perf_counter()
        detector = detector_class(
            graph_client=mock_client,
            detector_config=detector_config,
        )
        init_time = time.perf_counter() - start_init

        # Run detection
        start_detect = time.perf_counter()
        findings = detector.detect()
        detect_time = time.perf_counter() - start_detect

        total_time = init_time + detect_time

        # Summarize findings by severity
        severity_counts = {}
        for f in findings:
            sev = f.severity.value if hasattr(f.severity, 'value') else str(f.severity)
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        result = {
            "name": name,
            "status": "success",
            "init_time_ms": round(init_time * 1000, 2),
            "detect_time_ms": round(detect_time * 1000, 2),
            "total_time_ms": round(total_time * 1000, 2),
            "finding_count": len(findings),
            "severity_breakdown": severity_counts,
        }

        print(f"  Init time:   {result['init_time_ms']:>8.2f} ms")
        print(f"  Detect time: {result['detect_time_ms']:>8.2f} ms")
        print(f"  Total time:  {result['total_time_ms']:>8.2f} ms")
        print(f"  Findings:    {result['finding_count']}")
        if severity_counts:
            print(f"  Severities:  {severity_counts}")

        return result

    except Exception as e:
        print(f"  ERROR: {e}")
        return {
            "name": name,
            "status": "error",
            "error": str(e),
        }


def profile_external_tool(cmd: List[str], name: str, repo_path: Path, timeout: int = 120) -> Dict[str, Any]:
    """Profile an external tool directly.

    Args:
        cmd: Command to run
        name: Human-readable tool name
        repo_path: Path to repository
        timeout: Timeout in seconds

    Returns:
        Dict with timing information
    """
    print(f"\n{'='*60}")
    print(f"Profiling external tool: {name}")
    print(f"{'='*60}")

    try:
        start = time.perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - start

        # Try to count findings from output
        finding_count = 0
        if result.stdout:
            try:
                data = json.loads(result.stdout)
                if isinstance(data, list):
                    finding_count = len(data)
                elif isinstance(data, dict):
                    # ESLint format
                    if "vulnerabilities" in data:
                        finding_count = len(data["vulnerabilities"])
                    elif isinstance(data.get("results"), list):
                        finding_count = sum(len(r.get("messages", [])) for r in data["results"])
            except json.JSONDecodeError:
                # Count lines for non-JSON output
                finding_count = len([l for l in result.stdout.split('\n') if l.strip()])

        profile_result = {
            "name": name,
            "status": "success",
            "time_ms": round(elapsed * 1000, 2),
            "exit_code": result.returncode,
            "finding_count": finding_count,
        }

        print(f"  Time:        {profile_result['time_ms']:>8.2f} ms")
        print(f"  Exit code:   {result.returncode}")
        print(f"  Findings:    {finding_count}")

        return profile_result

    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT after {timeout}s")
        return {
            "name": name,
            "status": "timeout",
            "timeout_seconds": timeout,
        }
    except Exception as e:
        print(f"  ERROR: {e}")
        return {
            "name": name,
            "status": "error",
            "error": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="Profile TypeScript detectors")
    parser.add_argument("repo_path", help="Path to TypeScript repository")
    parser.add_argument("--output", "-o", help="Output JSON file")
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.exists():
        print(f"Error: Repository path does not exist: {repo_path}")
        sys.exit(1)

    # Count files
    ts_files = list(repo_path.rglob("*.ts")) + list(repo_path.rglob("*.tsx"))
    js_files = list(repo_path.rglob("*.js")) + list(repo_path.rglob("*.jsx"))

    print(f"\n{'#'*60}")
    print(f"# TypeScript Detector Profiling")
    print(f"# Repository: {repo_path}")
    print(f"# TypeScript files: {len(ts_files)}")
    print(f"# JavaScript files: {len(js_files)}")
    print(f"# Started: {datetime.now().isoformat()}")
    print(f"{'#'*60}")

    results = {
        "repo_path": str(repo_path),
        "ts_file_count": len(ts_files),
        "js_file_count": len(js_files),
        "timestamp": datetime.now().isoformat(),
        "detectors": [],
        "external_tools": [],
    }

    # Profile hybrid detectors
    print("\n\n" + "="*60)
    print("HYBRID DETECTORS (External Tool + Graph Enrichment)")
    print("="*60)

    from repotoire.detectors.eslint_detector import ESLintDetector
    from repotoire.detectors.tsc_detector import TscDetector
    from repotoire.detectors.npm_audit_detector import NpmAuditDetector
    from repotoire.detectors.jscpd_detector import JscpdDetector

    detectors = [
        (ESLintDetector, "ESLintDetector", {}),
        (TscDetector, "TscDetector", {}),
        (NpmAuditDetector, "NpmAuditDetector", {}),
        (JscpdDetector, "JscpdDetector", {"min_lines": 5, "min_tokens": 50}),
    ]

    for detector_class, name, config in detectors:
        result = profile_detector(detector_class, name, repo_path, config)
        results["detectors"].append(result)

    # Profile external tools directly (to isolate tool vs enrichment time)
    print("\n\n" + "="*60)
    print("EXTERNAL TOOLS (Raw execution)")
    print("="*60)

    # Check if bun is available
    try:
        subprocess.run(["bun", "--version"], capture_output=True, timeout=5)
        runner = "bunx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        runner = "npx"

    print(f"\nUsing JS runner: {runner}")

    external_tools = [
        ([runner, "eslint", "--format", "json", "."], "eslint (raw)"),
        ([runner, "tsc", "--noEmit", "--pretty", "false"], "tsc (raw)"),
        (["npm", "audit", "--json"], "npm audit (raw)"),
        ([runner, "jscpd", "--format", "json", "--min-lines", "5", "."], "jscpd (raw)"),
    ]

    for cmd, name in external_tools:
        result = profile_external_tool(cmd, name, repo_path)
        results["external_tools"].append(result)

    # Summary
    print("\n\n" + "#"*60)
    print("# SUMMARY")
    print("#"*60)

    print("\nHybrid Detectors:")
    print(f"{'Detector':<25} {'Time (ms)':>12} {'Findings':>10}")
    print("-" * 50)
    for r in results["detectors"]:
        time_str = f"{r.get('total_time_ms', 'N/A')}" if r["status"] == "success" else r["status"]
        findings = r.get("finding_count", "-")
        print(f"{r['name']:<25} {time_str:>12} {findings:>10}")

    print("\nExternal Tools (raw):")
    print(f"{'Tool':<25} {'Time (ms)':>12} {'Findings':>10}")
    print("-" * 50)
    for r in results["external_tools"]:
        time_str = f"{r.get('time_ms', 'N/A')}" if r["status"] == "success" else r["status"]
        findings = r.get("finding_count", "-")
        print(f"{r['name']:<25} {time_str:>12} {findings:>10}")

    # Overhead analysis
    print("\nOverhead Analysis (Detector vs Raw Tool):")
    detector_times = {r["name"]: r.get("total_time_ms", 0) for r in results["detectors"] if r["status"] == "success"}
    tool_times = {r["name"]: r.get("time_ms", 0) for r in results["external_tools"] if r["status"] == "success"}

    tool_mapping = {
        "ESLintDetector": "eslint (raw)",
        "TscDetector": "tsc (raw)",
        "NpmAuditDetector": "npm audit (raw)",
        "JscpdDetector": "jscpd (raw)",
    }

    for detector, tool in tool_mapping.items():
        if detector in detector_times and tool in tool_times:
            overhead = detector_times[detector] - tool_times[tool]
            pct = (overhead / tool_times[tool] * 100) if tool_times[tool] > 0 else 0
            print(f"  {detector}: {overhead:.2f} ms overhead ({pct:.1f}%)")

    # Save results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {args.output}")

    print("\n" + "#"*60)
    print(f"# Completed: {datetime.now().isoformat()}")
    print("#"*60)


if __name__ == "__main__":
    main()
