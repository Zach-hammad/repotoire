"""Security CLI commands for Repotoire.

Provides commands for:
- Vulnerability scanning
- SBOM generation
- Compliance reporting
- Security audits
"""

import click
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from repotoire.graph import Neo4jClient
from repotoire.security import (
    DependencyScanner,
    SBOMGenerator,
    ComplianceReporter,
    ComplianceFramework,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()


@click.group()
def security():
    """Security scanning and compliance reporting."""
    pass


@security.command("scan-deps")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--requirements",
    "-r",
    default="requirements.txt",
    help="Requirements file to scan (default: requirements.txt)",
)
@click.option(
    "--max-findings",
    "-m",
    default=100,
    type=int,
    help="Maximum findings to report (default: 100)",
)
@click.option(
    "--neo4j-uri",
    envvar="REPOTOIRE_NEO4J_URI",
    default="bolt://localhost:7687",
    help="Neo4j connection URI",
)
@click.option(
    "--neo4j-password",
    envvar="REPOTOIRE_NEO4J_PASSWORD",
    required=True,
    help="Neo4j password (or set REPOTOIRE_NEO4J_PASSWORD)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file for findings (JSON format)",
)
def scan_dependencies(
    repository_path,
    requirements,
    max_findings,
    neo4j_uri,
    neo4j_password,
    output,
):
    """Scan dependencies for known vulnerabilities.

    Uses pip-audit to check dependencies against OSV database
    for known CVEs and security vulnerabilities.

    Example:
        repotoire security scan-deps /path/to/repo
        repotoire security scan-deps /path/to/repo --requirements requirements-dev.txt
    """
    console.print("[bold blue]🔍 Scanning dependencies for vulnerabilities...[/bold blue]")

    try:
        # Initialize Neo4j client
        client = Neo4jClient(uri=neo4j_uri, password=neo4j_password)

        # Initialize scanner
        scanner = DependencyScanner(
            client,
            detector_config={
                "repository_path": repository_path,
                "requirements_file": requirements,
                "max_findings": max_findings,
            }
        )

        # Run scan
        findings = scanner.detect()

        if not findings:
            console.print("[bold green]✅ No vulnerabilities found![/bold green]")
            return

        # Display results
        console.print(f"\n[bold yellow]⚠️  Found {len(findings)} vulnerabilities:[/bold yellow]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Severity", style="bold")
        table.add_column("Package")
        table.add_column("Vulnerability")
        table.add_column("Fix Version")

        for finding in findings:
            severity_color = {
                "CRITICAL": "red",
                "HIGH": "orange1",
                "MEDIUM": "yellow",
                "LOW": "cyan",
            }.get(finding.severity.value, "white")

            package = finding.graph_context.get("package", "unknown")
            version = finding.graph_context.get("version", "unknown")
            vuln_id = finding.graph_context.get("vulnerability_id", "unknown")
            fix_versions = finding.graph_context.get("fix_versions", [])
            fix_version = ", ".join(fix_versions) if fix_versions else "No fix available"

            table.add_row(
                f"[{severity_color}]{finding.severity.value}[/{severity_color}]",
                f"{package} {version}",
                vuln_id,
                fix_version,
            )

        console.print(table)

        # Write to output file if specified
        if output:
            import json
            output_path = Path(output)
            with open(output_path, "w") as f:
                json.dump(
                    [
                        {
                            "id": f.id,
                            "severity": f.severity.value,
                            "title": f.title,
                            "description": f.description,
                            "package": f.graph_context.get("package"),
                            "version": f.graph_context.get("version"),
                            "vulnerability_id": f.graph_context.get("vulnerability_id"),
                            "fix_versions": f.graph_context.get("fix_versions", []),
                            "affected_files": f.affected_files,
                        }
                        for f in findings
                    ],
                    f,
                    indent=2,
                )
            console.print(f"\n[green]📄 Results written to: {output_path}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


@security.command("generate-sbom")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "xml"]),
    default="json",
    help="SBOM format (default: json)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path (default: sbom.{format} in repository)",
)
@click.option(
    "--requirements",
    "-r",
    default="requirements.txt",
    help="Requirements file (default: requirements.txt)",
)
def generate_sbom(repository_path, format, output, requirements):
    """Generate Software Bill of Materials (SBOM).

    Creates CycloneDX format SBOM for dependency tracking,
    compliance, and supply chain security.

    Example:
        repotoire security generate-sbom /path/to/repo
        repotoire security generate-sbom /path/to/repo --format xml --output sbom.xml
    """
    console.print("[bold blue]📦 Generating SBOM...[/bold blue]")

    try:
        # Initialize generator
        generator = SBOMGenerator({
            "repository_path": repository_path,
            "requirements_file": requirements,
            "output_format": format,
        })

        # Generate SBOM
        output_path = Path(output) if output else None
        sbom_path = generator.generate(output_path)

        # Get summary
        summary = generator.get_summary(sbom_path)

        console.print(f"\n[bold green]✅ SBOM generated successfully![/bold green]\n")
        console.print(f"[cyan]📄 Output file:[/cyan] {sbom_path}")
        console.print(f"[cyan]📊 Total components:[/cyan] {summary['total_components']}")

        if summary["licenses"]:
            console.print(f"[cyan]📜 Licenses found:[/cyan] {', '.join(summary['licenses'][:10])}")
            if len(summary["licenses"]) > 10:
                console.print(f"  ... and {len(summary['licenses']) - 10} more")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


@security.command("compliance-report")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--framework",
    "-f",
    type=click.Choice(["soc2", "iso27001", "pci_dss", "nist_csf", "cis"]),
    default="soc2",
    help="Compliance framework (default: soc2)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path (JSON format)",
)
@click.option(
    "--markdown",
    "-md",
    type=click.Path(),
    help="Generate markdown report",
)
@click.option(
    "--neo4j-uri",
    envvar="REPOTOIRE_NEO4J_URI",
    default="bolt://localhost:7687",
    help="Neo4j connection URI",
)
@click.option(
    "--neo4j-password",
    envvar="REPOTOIRE_NEO4J_PASSWORD",
    required=True,
    help="Neo4j password",
)
def compliance_report(
    repository_path,
    framework,
    output,
    markdown,
    neo4j_uri,
    neo4j_password,
):
    """Generate compliance report for security frameworks.

    Maps security findings to compliance requirements and generates
    audit-ready reports for SOC 2, ISO 27001, PCI DSS, etc.

    Example:
        repotoire security compliance-report /path/to/repo --framework soc2
        repotoire security compliance-report /path/to/repo -f pci_dss --markdown report.md
    """
    console.print(f"[bold blue]📋 Generating {framework.upper()} compliance report...[/bold blue]")

    try:
        # Get findings from analysis
        # For now, run a quick dependency scan
        client = Neo4jClient(uri=neo4j_uri, password=neo4j_password)
        scanner = DependencyScanner(
            client,
            detector_config={"repository_path": repository_path}
        )
        findings = scanner.detect()

        # Initialize reporter
        reporter = ComplianceReporter(
            framework=ComplianceFramework(framework),
            findings=findings,
            repository_path=Path(repository_path),
        )

        # Generate report
        report = reporter.generate_report(output_path=Path(output) if output else None)

        # Display summary
        summary = report["summary"]
        console.print(f"\n[bold]Compliance Summary:[/bold]\n")
        console.print(f"  [cyan]Score:[/cyan] {summary['compliance_score']}/100")
        console.print(f"  [cyan]Status:[/cyan] {summary['status'].replace('_', ' ').title()}")
        console.print(f"  [cyan]Total Findings:[/cyan] {summary['total_findings']}")

        console.print(f"\n[bold]Findings by Severity:[/bold]")
        for severity, count in summary["by_severity"].items():
            if count > 0:
                color = {
                    "critical": "red",
                    "high": "orange1",
                    "medium": "yellow",
                    "low": "cyan",
                }.get(severity, "white")
                console.print(f"  [{color}]{severity.title()}:[/{color}] {count}")

        # Display controls
        controls = report["controls"]
        passed = sum(1 for c in controls if c["status"] == "pass")
        failed = sum(1 for c in controls if c["status"] == "fail")

        console.print(f"\n[bold]Controls Assessment:[/bold]")
        console.print(f"  [green]✅ Passed:[/green] {passed}")
        console.print(f"  [red]❌ Failed:[/red] {failed}")

        if output:
            console.print(f"\n[green]📄 JSON report saved to: {output}[/green]")

        # Generate markdown if requested
        if markdown:
            md_report = reporter.generate_markdown_report(Path(markdown))
            console.print(f"[green]📄 Markdown report saved to: {markdown}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


@security.command("audit")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--neo4j-uri",
    envvar="REPOTOIRE_NEO4J_URI",
    default="bolt://localhost:7687",
    help="Neo4j connection URI",
)
@click.option(
    "--neo4j-password",
    envvar="REPOTOIRE_NEO4J_PASSWORD",
    required=True,
    help="Neo4j password",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(),
    help="Output directory for all reports",
)
def audit(repository_path, neo4j_uri, neo4j_password, output_dir):
    """Run comprehensive security audit.

    Performs:
    - Dependency vulnerability scan
    - SBOM generation
    - Compliance report (SOC 2)

    Example:
        repotoire security audit /path/to/repo
        repotoire security audit /path/to/repo --output-dir ./security-reports
    """
    console.print("[bold blue]🔒 Running comprehensive security audit...[/bold blue]\n")

    repo_path = Path(repository_path)
    output_path = Path(output_dir) if output_dir else repo_path / "security-audit"
    output_path.mkdir(parents=True, exist_ok=True)

    results = {"success": [], "failed": []}

    # 1. Dependency scan
    console.print("[bold]1. Scanning dependencies...[/bold]")
    try:
        client = Neo4jClient(uri=neo4j_uri, password=neo4j_password)
        scanner = DependencyScanner(
            client,
            detector_config={"repository_path": str(repo_path)}
        )
        findings = scanner.detect()

        vuln_file = output_path / "vulnerabilities.json"
        import json
        with open(vuln_file, "w") as f:
            json.dump([{
                "severity": f.severity.value,
                "title": f.title,
                "package": f.graph_context.get("package"),
                "vulnerability_id": f.graph_context.get("vulnerability_id"),
            } for f in findings], f, indent=2)

        console.print(f"  [green]✅ Found {len(findings)} vulnerabilities → {vuln_file}[/green]")
        results["success"].append("Dependency scan")
    except Exception as e:
        console.print(f"  [red]❌ Failed: {e}[/red]")
        results["failed"].append("Dependency scan")

    # 2. SBOM generation
    console.print("\n[bold]2. Generating SBOM...[/bold]")
    try:
        generator = SBOMGenerator({
            "repository_path": str(repo_path),
            "output_format": "json",
        })
        sbom_path = generator.generate(output_path / "sbom.json")
        console.print(f"  [green]✅ SBOM generated → {sbom_path}[/green]")
        results["success"].append("SBOM generation")
    except Exception as e:
        console.print(f"  [red]❌ Failed: {e}[/red]")
        results["failed"].append("SBOM generation")

    # 3. Compliance report
    console.print("\n[bold]3. Generating compliance report...[/bold]")
    try:
        reporter = ComplianceReporter(
            framework=ComplianceFramework.SOC2,
            findings=findings if 'findings' in locals() else [],
            repository_path=repo_path,
        )
        report_file = output_path / "compliance-soc2.json"
        md_file = output_path / "compliance-soc2.md"
        reporter.generate_report(report_file)
        reporter.generate_markdown_report(md_file)
        console.print(f"  [green]✅ Compliance report → {report_file}, {md_file}[/green]")
        results["success"].append("Compliance report")
    except Exception as e:
        console.print(f"  [red]❌ Failed: {e}[/red]")
        results["failed"].append("Compliance report")

    # Summary
    console.print(f"\n[bold]Security Audit Complete![/bold]")
    console.print(f"  [green]✅ Successful: {len(results['success'])}[/green]")
    if results["failed"]:
        console.print(f"  [red]❌ Failed: {len(results['failed'])}[/red]")
    console.print(f"\n[cyan]📂 Reports saved to: {output_path}[/cyan]")
