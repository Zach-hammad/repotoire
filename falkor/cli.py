"""Command-line interface for Falkor."""

import click
import logging
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from falkor.pipeline import IngestionPipeline
from falkor.graph import Neo4jClient
from falkor.detectors import AnalysisEngine

console = Console()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """Falkor - Graph-Powered Code Health Platform"""
    pass


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option(
    "--neo4j-uri", default="bolt://localhost:7687", help="Neo4j connection URI"
)
@click.option("--neo4j-user", default="neo4j", help="Neo4j username")
@click.option(
    "--neo4j-password",
    prompt=True,
    hide_input=True,
    help="Neo4j password",
)
@click.option(
    "--pattern",
    "-p",
    multiple=True,
    default=["**/*.py"],
    help="File patterns to analyze",
)
def ingest(
    repo_path: str, neo4j_uri: str, neo4j_user: str, neo4j_password: str, pattern: tuple
) -> None:
    """Ingest a codebase into the knowledge graph."""
    console.print(f"\n[bold cyan]🐉 Falkor Ingestion[/bold cyan]\n")
    console.print(f"Repository: {repo_path}")
    console.print(f"Patterns: {', '.join(pattern)}\n")

    with Neo4jClient(neo4j_uri, neo4j_user, neo4j_password) as db:
        pipeline = IngestionPipeline(repo_path, db)
        pipeline.ingest(patterns=list(pattern))

        # Show stats
        stats = db.get_stats()
        table = Table(title="Ingestion Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", style="green")

        for key, value in stats.items():
            table.add_row(key.replace("_", " ").title(), str(value))

        console.print(table)


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option(
    "--neo4j-uri", default="bolt://localhost:7687", help="Neo4j connection URI"
)
@click.option("--neo4j-user", default="neo4j", help="Neo4j username")
@click.option(
    "--neo4j-password",
    prompt=True,
    hide_input=True,
    help="Neo4j password",
)
@click.option(
    "--output", "-o", type=click.Path(), help="Output file for JSON report"
)
def analyze(
    repo_path: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    output: str | None,
) -> None:
    """Analyze codebase health and generate report."""
    console.print(f"\n[bold cyan]🐉 Falkor Analysis[/bold cyan]\n")

    with Neo4jClient(neo4j_uri, neo4j_user, neo4j_password) as db:
        engine = AnalysisEngine(db)
        health = engine.analyze()

        # Display results
        _display_health_report(health)

        # Save to file if requested
        if output:
            import json

            with open(output, "w") as f:
                json.dump(health.to_dict(), f, indent=2)
            console.print(f"\n✅ Report saved to {output}")


def _display_health_report(health) -> None:
    """Display health report in terminal."""
    # Overall grade
    grade_colors = {"A": "green", "B": "cyan", "C": "yellow", "D": "orange", "F": "red"}
    grade_color = grade_colors.get(health.grade, "white")

    console.print(
        Panel(
            f"[bold {grade_color}]Grade: {health.grade}[/bold {grade_color}]\n"
            f"Score: {health.overall_score:.1f}/100",
            title="Overall Health",
            border_style=grade_color,
        )
    )

    # Category scores
    scores_table = Table(title="Category Scores")
    scores_table.add_column("Category", style="cyan")
    scores_table.add_column("Weight", style="dim")
    scores_table.add_column("Score", style="green")
    scores_table.add_column("Progress", style="blue")

    categories = [
        ("Graph Structure", "40%", health.structure_score),
        ("Code Quality", "30%", health.quality_score),
        ("Architecture Health", "30%", health.architecture_score),
    ]

    for name, weight, score in categories:
        progress = "█" * int(score / 10) + "░" * (10 - int(score / 10))
        scores_table.add_row(name, weight, f"{score:.1f}/100", progress)

    console.print(scores_table)

    # Key metrics
    m = health.metrics
    metrics_table = Table(title="Key Metrics")
    metrics_table.add_column("Metric", style="cyan")
    metrics_table.add_column("Value", style="green")
    metrics_table.add_column("Status", style="yellow")

    metrics = [
        ("Files", m.total_files, ""),
        ("Classes", m.total_classes, ""),
        ("Functions", m.total_functions, ""),
        ("Modularity", f"{m.modularity:.2f}", "Good" if m.modularity > 0.6 else "Low"),
        ("Avg Coupling", f"{m.avg_coupling:.1f}" if m.avg_coupling is not None else "N/A", ""),
        ("Circular Dependencies", m.circular_dependencies, "⚠️" if m.circular_dependencies > 0 else "✓"),
        ("God Classes", m.god_class_count, "⚠️" if m.god_class_count > 0 else "✓"),
    ]

    for metric, value, status in metrics:
        metrics_table.add_row(metric, str(value), status)

    console.print(metrics_table)

    # Findings summary
    fs = health.findings_summary
    if fs.total > 0:
        findings_table = Table(title="Findings Summary")
        findings_table.add_column("Severity", style="bold")
        findings_table.add_column("Count", style="cyan")

        if fs.critical > 0:
            findings_table.add_row("[red]Critical[/red]", str(fs.critical))
        if fs.high > 0:
            findings_table.add_row("[orange]High[/orange]", str(fs.high))
        if fs.medium > 0:
            findings_table.add_row("[yellow]Medium[/yellow]", str(fs.medium))
        if fs.low > 0:
            findings_table.add_row("[blue]Low[/blue]", str(fs.low))

        console.print(findings_table)


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
