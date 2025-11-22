#!/usr/bin/env python3
"""GitHub PR analyzer for Repotoire.

Analyzes code changes in a PR and generates markdown comments and JSON output.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any
import os

from repotoire.graph import Neo4jClient
from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.detectors.engine import AnalysisEngine
from repotoire.models import Severity, CodebaseHealth
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


def parse_severity(severity_str: str) -> Severity:
    """Parse severity string to Severity enum."""
    severity_map = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
    }
    return severity_map.get(severity_str.lower(), Severity.CRITICAL)


def format_pr_comment(health: CodebaseHealth, fail_on: Severity, files: List[str]) -> str:
    """Format findings as GitHub PR comment markdown.

    Args:
        health: CodebaseHealth with findings
        fail_on: Severity threshold for failing
        files: List of changed files

    Returns:
        Markdown formatted comment
    """
    # Filter findings for changed files
    relevant_findings = [
        f for f in health.findings
        if any(af in files for af in f.affected_files)
    ] if files else health.findings

    # Group by severity
    by_severity = {
        Severity.CRITICAL: [],
        Severity.HIGH: [],
        Severity.MEDIUM: [],
        Severity.LOW: [],
        Severity.INFO: [],
    }

    for finding in relevant_findings:
        by_severity[finding.severity].append(finding)

    # Severity icons
    icons = {
        Severity.CRITICAL: "🔴",
        Severity.HIGH: "🟠",
        Severity.MEDIUM: "🟡",
        Severity.LOW: "🟢",
        Severity.INFO: "ℹ️",
    }

    # Build comment
    lines = [
        "## 🤖 Repotoire Code Quality Report",
        "",
        f"**Health Score**: {health.overall_score}/100",
        "",
    ]

    # Summary
    total = len(relevant_findings)
    critical = len(by_severity[Severity.CRITICAL])
    high = len(by_severity[Severity.HIGH])
    medium = len(by_severity[Severity.MEDIUM])
    low = len(by_severity[Severity.LOW])

    if total == 0:
        lines.extend([
            "### ✅ No Issues Found",
            "",
            "Great job! No code quality issues detected in the changed files.",
        ])
        return "\n".join(lines)

    lines.extend([
        f"### 📊 Found {total} issue(s)",
        "",
        f"- {icons[Severity.CRITICAL]} **Critical**: {critical}",
        f"- {icons[Severity.HIGH]} **High**: {high}",
        f"- {icons[Severity.MEDIUM]} **Medium**: {medium}",
        f"- {icons[Severity.LOW]} **Low**: {low}",
        "",
    ])

    # Show top 5 critical/high issues
    critical_findings = by_severity[Severity.CRITICAL] + by_severity[Severity.HIGH]
    if critical_findings:
        lines.extend([
            "### ⚠️ Critical & High Priority Issues",
            "",
        ])

        for finding in critical_findings[:5]:
            icon = icons[finding.severity]
            files_str = ", ".join(finding.affected_files[:2])
            if len(finding.affected_files) > 2:
                files_str += f" (+{len(finding.affected_files) - 2} more)"

            lines.extend([
                f"#### {icon} {finding.title}",
                "",
                f"**Severity**: {finding.severity.name}  ",
                f"**Files**: {files_str}  ",
                f"**Description**: {finding.description}",
                "",
            ])

            if finding.suggested_fix:
                lines.extend([
                    f"**💡 Suggested Fix**: {finding.suggested_fix}",
                    "",
                ])

        if len(critical_findings) > 5:
            lines.extend([
                f"<details>",
                f"<summary>Show {len(critical_findings) - 5} more critical/high issues</summary>",
                "",
            ])

            for finding in critical_findings[5:]:
                icon = icons[finding.severity]
                lines.append(f"- {icon} **{finding.title}** in {', '.join(finding.affected_files[:2])}")

            lines.extend([
                "",
                "</details>",
                "",
            ])

    # Status
    critical_above_threshold = [
        f for f in relevant_findings
        if f.severity.value <= fail_on.value
    ]

    if critical_above_threshold:
        lines.extend([
            f"### ❌ Check Failed",
            "",
            f"Found {len(critical_above_threshold)} issue(s) at or above `{fail_on.name}` severity threshold.",
            "",
            "Please fix these issues before merging.",
        ])
    else:
        lines.extend([
            f"### ✅ Check Passed",
            "",
            f"All issues are below the `{fail_on.name}` severity threshold.",
        ])

    lines.extend([
        "",
        "---",
        "",
        "*Generated by [Repotoire](https://github.com/yourusername/repotoire)*",
    ])

    return "\n".join(lines)


def main() -> int:
    """Main entry point for PR analyzer.

    Returns:
        0 if check passes, 1 if check fails
    """
    parser = argparse.ArgumentParser(
        description="Analyze code quality for GitHub PR"
    )
    parser.add_argument(
        "--repo-path",
        required=True,
        help="Path to repository root"
    )
    parser.add_argument(
        "--fail-on",
        default="critical",
        choices=["critical", "high", "medium", "low"],
        help="Minimum severity to fail (default: critical)"
    )
    parser.add_argument(
        "--files",
        nargs="*",
        help="Specific files to analyze"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output JSON file"
    )
    parser.add_argument(
        "--pr-comment",
        required=True,
        help="Path to output PR comment markdown"
    )
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j URI"
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.getenv("REPOTOIRE_NEO4J_PASSWORD"),
        help="Neo4j password"
    )

    args = parser.parse_args()

    if not args.neo4j_password:
        print("❌ Error: Neo4j password not provided")
        print("   Set REPOTOIRE_NEO4J_PASSWORD environment variable")
        return 1

    try:
        # Connect to Neo4j
        client = Neo4jClient(uri=args.neo4j_uri, password=args.neo4j_password)

        # Run ingestion
        print(f"📥 Ingesting codebase...")
        pipeline = IngestionPipeline(
            repo_path=args.repo_path,
            neo4j_client=client,
            batch_size=100
        )
        pipeline.ingest(incremental=True)

        # Run analysis
        print(f"🔍 Analyzing code...")
        engine = AnalysisEngine(
            neo4j_client=client,
            repository_path=args.repo_path
        )
        health = engine.analyze()

        # Filter for specific files if provided
        files = args.files or []
        relevant_findings = [
            f for f in health.findings
            if not files or any(af in files for af in f.affected_files)
        ]

        # Determine pass/fail
        fail_severity = parse_severity(args.fail_on)
        failing_findings = [
            f for f in relevant_findings
            if f.severity.value <= fail_severity.value
        ]

        # Generate PR comment
        comment = format_pr_comment(health, fail_severity, files)
        Path(args.pr_comment).write_text(comment)

        # Generate JSON output
        output_data = {
            "findings_count": len(relevant_findings),
            "critical_count": len([f for f in relevant_findings if f.severity == Severity.CRITICAL]),
            "high_count": len([f for f in relevant_findings if f.severity == Severity.HIGH]),
            "medium_count": len([f for f in relevant_findings if f.severity == Severity.MEDIUM]),
            "low_count": len([f for f in relevant_findings if f.severity == Severity.LOW]),
            "health_score": health.overall_score,
            "pass": len(failing_findings) == 0,
        }
        Path(args.output).write_text(json.dumps(output_data, indent=2))

        # Print summary
        print(f"\n📊 Analysis Results:")
        print(f"   Total findings: {len(relevant_findings)}")
        print(f"   Critical: {output_data['critical_count']}")
        print(f"   Health score: {output_data['health_score']}/100")
        print(f"   Status: {'✅ PASS' if output_data['pass'] else '❌ FAIL'}")

        return 0 if output_data['pass'] else 1

    except Exception as e:
        print(f"❌ Error during analysis: {e}")
        logger.exception("PR analysis failed")
        return 1
    finally:
        if 'client' in locals():
            client.close()


if __name__ == "__main__":
    sys.exit(main())
