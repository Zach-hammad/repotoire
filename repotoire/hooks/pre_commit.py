#!/usr/bin/env python3
"""Pre-commit hook for Repotoire code quality checks.

This script is designed to be called by the pre-commit framework.
It analyzes staged Python files and blocks commits if critical issues are found.
"""

import sys
import argparse
import tempfile
import shutil
from pathlib import Path
from typing import List, Set
import subprocess

from repotoire.graph import Neo4jClient
from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.detectors.engine import AnalysisEngine
from repotoire.models import Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


def get_staged_files() -> List[str]:
    """Get list of staged Python files from git.

    Returns:
        List of file paths relative to repo root
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True
        )
        files = result.stdout.strip().split("\n")
        # Filter for Python files only
        return [f for f in files if f.endswith(".py") and f]
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get staged files: {e}")
        return []


def parse_severity(severity_str: str) -> Severity:
    """Parse severity string to Severity enum.

    Args:
        severity_str: Severity as string (critical, high, medium, low)

    Returns:
        Severity enum value
    """
    severity_map = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
    }
    return severity_map.get(severity_str.lower(), Severity.MEDIUM)


def format_finding_output(finding) -> str:
    """Format a finding for terminal output.

    Args:
        finding: Finding object

    Returns:
        Formatted string for display
    """
    severity_icons = {
        Severity.CRITICAL: "🔴",
        Severity.HIGH: "🟠",
        Severity.MEDIUM: "🟡",
        Severity.LOW: "🟢",
        Severity.INFO: "ℹ️",
    }

    icon = severity_icons.get(finding.severity, "•")

    # Format file locations
    files = ", ".join(finding.affected_files[:3])
    if len(finding.affected_files) > 3:
        files += f" (+{len(finding.affected_files) - 3} more)"

    output = f"\n{icon} [{finding.severity.name}] {finding.title}\n"
    output += f"   Files: {files}\n"
    output += f"   {finding.description}\n"

    if finding.suggested_fix:
        output += f"   💡 Fix: {finding.suggested_fix}\n"

    return output


def main() -> int:
    """Main entry point for pre-commit hook.

    Returns:
        0 if checks pass, 1 if critical issues found
    """
    parser = argparse.ArgumentParser(
        description="Repotoire pre-commit hook for code quality checks"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to check (provided by pre-commit framework)"
    )
    parser.add_argument(
        "--fail-on",
        default="critical",
        choices=["critical", "high", "medium", "low"],
        help="Minimum severity level to fail the commit (default: critical)"
    )
    parser.add_argument(
        "--neo4j-uri",
        default="bolt://localhost:7687",
        help="Neo4j connection URI"
    )
    parser.add_argument(
        "--neo4j-password",
        help="Neo4j password (defaults to REPOTOIRE_NEO4J_PASSWORD env var)"
    )
    parser.add_argument(
        "--skip-ingestion",
        action="store_true",
        help="Skip ingestion and only run analysis (assumes data already in graph)"
    )

    args = parser.parse_args()

    # Get staged files from git if not provided
    files_to_check = args.files if args.files else get_staged_files()

    if not files_to_check:
        print("✅ No Python files to check")
        return 0

    print(f"🔍 Checking {len(files_to_check)} staged file(s)...")

    # Get Neo4j password from args or environment
    import os
    neo4j_password = args.neo4j_password or os.getenv("REPOTOIRE_NEO4J_PASSWORD")

    if not neo4j_password:
        print("❌ Error: NEO4J_PASSWORD not provided")
        print("   Set REPOTOIRE_NEO4J_PASSWORD environment variable or use --neo4j-password")
        return 1

    try:
        # Connect to Neo4j
        client = Neo4jClient(uri=args.neo4j_uri, password=neo4j_password)

        # Get repository root
        repo_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()

        if not args.skip_ingestion:
            # Run incremental ingestion on staged files
            print("   Analyzing code...")
            pipeline = IngestionPipeline(
                repo_path=repo_root,
                neo4j_client=client,
                batch_size=50
            )

            # Only ingest the staged files
            pipeline.ingest(incremental=True)

        # Run analysis
        engine = AnalysisEngine(
            neo4j_client=client,
            repository_path=repo_root
        )

        health = engine.analyze()

        # Filter findings for staged files only
        staged_file_set = set(files_to_check)
        relevant_findings = [
            f for f in health.findings
            if any(af in staged_file_set for af in f.affected_files)
        ]

        # Check severity threshold
        fail_severity = parse_severity(args.fail_on)
        critical_findings = [
            f for f in relevant_findings
            if f.severity.value <= fail_severity.value
        ]

        # Display results
        if not relevant_findings:
            print("✅ No issues found in staged files")
            return 0

        print(f"\n📊 Found {len(relevant_findings)} issue(s) in staged files:")

        # Show all findings
        for finding in relevant_findings:
            print(format_finding_output(finding))

        # Determine pass/fail
        if critical_findings:
            print(f"\n❌ Commit blocked: {len(critical_findings)} issue(s) at or above '{args.fail_on}' severity")
            print(f"   Fix the issues above or use 'git commit --no-verify' to bypass")
            return 1
        else:
            print(f"\n⚠️  Warning: Found {len(relevant_findings)} issue(s) below '{args.fail_on}' threshold")
            print("✅ Commit allowed")
            return 0

    except Exception as e:
        print(f"❌ Error during pre-commit check: {e}")
        logger.exception("Pre-commit hook failed")
        return 1
    finally:
        if 'client' in locals():
            client.close()


if __name__ == "__main__":
    sys.exit(main())
