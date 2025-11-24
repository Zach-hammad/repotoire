"""Monorepo CLI commands for Repotoire.

Provides commands for:
- Package detection and listing
- Per-package analysis
- Affected packages detection
- Cross-package analysis
- Build impact recommendations
"""

import click
import json
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.tree import Tree

from repotoire.graph import Neo4jClient
from repotoire.monorepo import (
    PackageDetector,
    PackageAnalyzer,
    AffectedPackagesDetector,
    CrossPackageAnalyzer,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()


@click.group()
def monorepo():
    """Monorepo analysis and optimization."""
    pass


@monorepo.command("detect-packages")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file for package list (JSON format)",
)
def detect_packages(repository_path, output):
    """Detect packages in a monorepo.

    Scans for package.json, pyproject.toml, BUILD files, etc. to identify
    all packages in the monorepo.

    Example:
        repotoire monorepo detect-packages /path/to/monorepo
        repotoire monorepo detect-packages /path/to/monorepo --output packages.json
    """
    console.print("[bold blue]📦 Detecting packages in monorepo...[/bold blue]")

    try:
        detector = PackageDetector(Path(repository_path))
        packages = detector.detect_packages()

        if not packages:
            console.print("[yellow]⚠️  No packages detected[/yellow]")
            return

        # Display results
        console.print(f"\n[bold green]✅ Detected {len(packages)} packages:[/bold green]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Package", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Path")
        table.add_column("Files", justify="right")
        table.add_column("LOC", justify="right")

        for package in packages:
            table.add_row(
                package.name,
                package.metadata.package_type,
                package.path,
                str(len(package.files)),
                f"{package.loc:,}",
            )

        console.print(table)

        # Show dependency summary
        total_imports = sum(len(p.imports_packages) for p in packages)
        console.print(f"\n[cyan]📊 Total package dependencies:[/cyan] {total_imports}")

        # Write to output file if specified
        if output:
            output_data = [p.to_dict() for p in packages]
            output_path = Path(output)
            with open(output_path, "w") as f:
                json.dump(output_data, f, indent=2)
            console.print(f"\n[green]📄 Package list saved to: {output_path}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


@monorepo.command("analyze")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--package",
    "-p",
    help="Analyze specific package (path or name)",
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
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file for results (JSON format)",
)
def analyze_monorepo(repository_path, package, neo4j_uri, neo4j_password, output):
    """Analyze monorepo packages with per-package health scores.

    Provides detailed health analysis for each package in the monorepo,
    including coupling scores, independence metrics, and affected packages.

    Example:
        repotoire monorepo analyze /path/to/monorepo
        repotoire monorepo analyze /path/to/monorepo --package packages/auth
    """
    console.print("[bold blue]🔍 Analyzing monorepo packages...[/bold blue]\n")

    try:
        # Connect to Neo4j
        client = Neo4jClient(uri=neo4j_uri, password=neo4j_password)

        # Detect packages
        detector = PackageDetector(Path(repository_path))
        packages = detector.detect_packages()

        if not packages:
            console.print("[yellow]⚠️  No packages detected[/yellow]")
            return

        # Initialize analyzer
        analyzer = PackageAnalyzer(client, repository_path)

        if package:
            # Analyze specific package
            target_pkg = None
            for pkg in packages:
                if pkg.path == package or pkg.name == package:
                    target_pkg = pkg
                    break

            if not target_pkg:
                console.print(f"[red]❌ Package not found: {package}[/red]")
                return

            console.print(f"[bold]Analyzing package:[/bold] {target_pkg.name}\n")
            package_health = analyzer.analyze_package(target_pkg)

            # Display results
            _display_package_health(package_health)

            if output:
                output_path = Path(output)
                with open(output_path, "w") as f:
                    json.dump(package_health.to_dict(), f, indent=2)
                console.print(f"\n[green]📄 Results saved to: {output_path}[/green]")

        else:
            # Analyze all packages
            monorepo_health = analyzer.analyze_monorepo(packages)

            # Display summary
            console.print(Panel(
                f"[bold]Overall Grade:[/bold] {monorepo_health.grade} ({monorepo_health.overall_score:.1f}/100)\n"
                f"[bold]Average Package Score:[/bold] {monorepo_health.avg_package_score:.1f}/100\n"
                f"[bold]Packages:[/bold] {monorepo_health.package_count}\n"
                f"[bold]Cross-Package Issues:[/bold] {monorepo_health.cross_package_issues}\n"
                f"[bold]Circular Dependencies:[/bold] {monorepo_health.circular_package_dependencies}",
                title="Monorepo Health Summary",
                border_style="green",
            ))

            # Display per-package scores
            console.print("\n[bold]Package Health Scores:[/bold]\n")

            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Package", style="cyan")
            table.add_column("Grade", justify="center")
            table.add_column("Score", justify="right")
            table.add_column("Coupling", justify="right")
            table.add_column("Independence", justify="right")
            table.add_column("Test Coverage", justify="right")

            for pkg_health in sorted(
                monorepo_health.package_health_scores,
                key=lambda x: x.overall_score,
                reverse=True,
            ):
                # Color grade based on score
                grade_color = {
                    "A": "green",
                    "B": "cyan",
                    "C": "yellow",
                    "D": "orange1",
                    "F": "red",
                }.get(pkg_health.grade, "white")

                table.add_row(
                    pkg_health.package_name,
                    f"[{grade_color}]{pkg_health.grade}[/{grade_color}]",
                    f"{pkg_health.overall_score:.1f}",
                    f"{pkg_health.coupling_score:.1f}",
                    f"{pkg_health.independence_score:.1f}",
                    f"{pkg_health.test_coverage:.1f}%",
                )

            console.print(table)

            if output:
                output_path = Path(output)
                with open(output_path, "w") as f:
                    json.dump(monorepo_health.to_dict(), f, indent=2)
                console.print(f"\n[green]📄 Results saved to: {output_path}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


@monorepo.command("affected")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--since",
    "-s",
    default="origin/main",
    help="Git reference to compare against (default: origin/main)",
)
@click.option(
    "--max-depth",
    "-d",
    default=10,
    type=int,
    help="Maximum dependency traversal depth (default: 10)",
)
@click.option(
    "--show-commands",
    "-c",
    is_flag=True,
    help="Show build/test commands for affected packages",
)
@click.option(
    "--tool",
    "-t",
    type=click.Choice(["auto", "nx", "turborepo", "lerna"]),
    default="auto",
    help="Monorepo tool to generate commands for (default: auto-detect)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file for results (JSON format)",
)
def affected_packages(repository_path, since, max_depth, show_commands, tool, output):
    """Detect packages affected by code changes.

    Uses git to find changed files and dependency graph to determine
    which packages need to be tested/rebuilt.

    Example:
        repotoire monorepo affected /path/to/monorepo --since main
        repotoire monorepo affected /path/to/monorepo --since HEAD~5 --show-commands
    """
    console.print(f"[bold blue]🔍 Detecting affected packages since {since}...[/bold blue]\n")

    try:
        # Detect packages
        detector_pkg = PackageDetector(Path(repository_path))
        packages = detector_pkg.detect_packages()

        if not packages:
            console.print("[yellow]⚠️  No packages detected[/yellow]")
            return

        # Detect affected packages
        detector = AffectedPackagesDetector(Path(repository_path), packages)
        result = detector.detect_affected_since(since, max_depth=max_depth)

        # Display results
        console.print(Panel(
            f"[bold]Changed Files:[/bold] {result['stats']['changed_files']}\n"
            f"[bold]Changed Packages:[/bold] {result['stats']['changed_packages']}\n"
            f"[bold]Affected Packages:[/bold] {result['stats']['affected_packages']}\n"
            f"[bold]Total to Test/Build:[/bold] {result['stats']['total_packages']}",
            title="Affected Packages Summary",
            border_style="cyan",
        ))

        if result['changed']:
            console.print("\n[bold yellow]📦 Changed Packages:[/bold yellow]")
            for pkg_path in result['changed']:
                console.print(f"  • {pkg_path}")

        if result['affected']:
            console.print("\n[bold cyan]🔗 Affected Packages (dependents):[/bold cyan]")
            for pkg_path in result['affected']:
                console.print(f"  • {pkg_path}")

        # Show build commands if requested
        if show_commands and result['all']:
            commands = detector.generate_build_commands(result, tool=tool)

            console.print("\n[bold green]🛠️  Recommended Build Commands:[/bold green]")
            for cmd in commands:
                console.print(f"  {cmd}")

        # Write to output file if specified
        if output:
            output_path = Path(output)
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            console.print(f"\n[green]📄 Results saved to: {output_path}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


@monorepo.command("cross-package")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file for findings (JSON format)",
)
def cross_package_analysis(repository_path, output):
    """Analyze cross-package issues.

    Detects problems spanning multiple packages:
    - Circular dependencies between packages
    - Excessive package coupling
    - Package boundary violations
    - Inconsistent dependency versions

    Example:
        repotoire monorepo cross-package /path/to/monorepo
        repotoire monorepo cross-package /path/to/monorepo --output issues.json
    """
    console.print("[bold blue]🔍 Analyzing cross-package issues...[/bold blue]\n")

    try:
        # Detect packages
        detector = PackageDetector(Path(repository_path))
        packages = detector.detect_packages()

        if not packages:
            console.print("[yellow]⚠️  No packages detected[/yellow]")
            return

        # Analyze cross-package issues
        analyzer = CrossPackageAnalyzer(packages)
        findings = analyzer.detect_cross_package_issues()

        if not findings:
            console.print("[bold green]✅ No cross-package issues found![/bold green]")
            return

        # Display results
        console.print(f"[bold yellow]⚠️  Found {len(findings)} cross-package issues:[/bold yellow]\n")

        # Group by severity
        by_severity = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": [], "INFO": []}
        for finding in findings:
            by_severity[finding.severity.value.upper()].append(finding)

        for severity, severity_findings in by_severity.items():
            if not severity_findings:
                continue

            severity_color = {
                "CRITICAL": "red",
                "HIGH": "orange1",
                "MEDIUM": "yellow",
                "LOW": "cyan",
                "INFO": "white",
            }.get(severity, "white")

            console.print(f"[bold {severity_color}]{severity} ({len(severity_findings)}):[/bold {severity_color}]")

            for finding in severity_findings:
                console.print(f"  • {finding.title}")
                console.print(f"    {finding.description[:150]}...")
                console.print()

        # Write to output file if specified
        if output:
            output_data = [
                {
                    "id": f.id,
                    "severity": f.severity.value,
                    "title": f.title,
                    "description": f.description,
                    "suggested_fix": f.suggested_fix,
                    "graph_context": f.graph_context,
                }
                for f in findings
            ]
            output_path = Path(output)
            with open(output_path, "w") as f:
                json.dump(output_data, f, indent=2)
            console.print(f"[green]📄 Findings saved to: {output_path}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


@monorepo.command("deps")
@click.argument("repository_path", type=click.Path(exists=True))
@click.option(
    "--visualize",
    "-v",
    is_flag=True,
    help="Visualize dependency graph as tree",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file for dependency graph (JSON format)",
)
def dependency_graph(repository_path, visualize, output):
    """Show package dependency graph.

    Displays dependencies between packages in the monorepo.

    Example:
        repotoire monorepo deps /path/to/monorepo --visualize
        repotoire monorepo deps /path/to/monorepo --output deps.json
    """
    console.print("[bold blue]📊 Analyzing package dependencies...[/bold blue]\n")

    try:
        # Detect packages
        detector_pkg = PackageDetector(Path(repository_path))
        packages = detector_pkg.detect_packages()

        if not packages:
            console.print("[yellow]⚠️  No packages detected[/yellow]")
            return

        # Get dependency graph
        detector = AffectedPackagesDetector(Path(repository_path), packages)
        graph = detector.get_dependency_graph()

        if visualize:
            # Display as tree
            tree = Tree("[bold]📦 Package Dependencies[/bold]")

            for pkg_path, deps in graph.items():
                pkg_node = tree.add(f"[cyan]{deps['name']}[/cyan] ({pkg_path})")

                if deps['imports']:
                    imports_node = pkg_node.add("[yellow]Imports:[/yellow]")
                    for imp in deps['imports']:
                        imp_name = graph[imp]['name'] if imp in graph else imp
                        imports_node.add(f"→ {imp_name}")

                if deps['imported_by']:
                    imported_by_node = pkg_node.add("[green]Imported by:[/green]")
                    for imp_by in deps['imported_by']:
                        imp_by_name = graph[imp_by]['name'] if imp_by in graph else imp_by
                        imported_by_node.add(f"← {imp_by_name}")

            console.print(tree)

        else:
            # Display as table
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Package", style="cyan")
            table.add_column("Imports", justify="right")
            table.add_column("Imported By", justify="right")

            for pkg_path, deps in sorted(graph.items()):
                table.add_row(
                    deps['name'],
                    str(len(deps['imports'])),
                    str(len(deps['imported_by'])),
                )

            console.print(table)

        # Write to output file if specified
        if output:
            output_path = Path(output)
            with open(output_path, "w") as f:
                json.dump(graph, f, indent=2)
            console.print(f"\n[green]📄 Dependency graph saved to: {output_path}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Error: {e}[/bold red]")
        raise click.ClickException(str(e))


def _display_package_health(package_health):
    """Display package health details."""
    grade_color = {
        "A": "green",
        "B": "cyan",
        "C": "yellow",
        "D": "orange1",
        "F": "red",
    }.get(package_health.grade, "white")

    console.print(Panel(
        f"[bold]Grade:[/bold] [{grade_color}]{package_health.grade}[/{grade_color}] ({package_health.overall_score:.1f}/100)\n"
        f"[bold]Coupling Score:[/bold] {package_health.coupling_score:.1f}/100\n"
        f"[bold]Independence Score:[/bold] {package_health.independence_score:.1f}/100\n"
        f"[bold]Test Coverage:[/bold] {package_health.test_coverage:.1f}%\n"
        f"[bold]Build Time Estimate:[/bold] {package_health.build_time_estimate}s\n"
        f"[bold]Affected Packages:[/bold] {len(package_health.affected_by_changes)}",
        title=f"Package Health: {package_health.package_name}",
        border_style=grade_color,
    ))

    # Show affected packages if any
    if package_health.affected_by_changes:
        console.print("\n[bold yellow]⚠️  Changes here will affect:[/bold yellow]")
        for affected_pkg in package_health.affected_by_changes:
            console.print(f"  • {affected_pkg}")
