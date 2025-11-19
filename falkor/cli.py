"""Command-line interface for Falkor."""

import click
import os
from dataclasses import asdict
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.tree import Tree
from rich.syntax import Syntax
from rich.layout import Layout
from rich.text import Text
from rich import box

from falkor.pipeline import IngestionPipeline
from falkor.graph import Neo4jClient
from falkor.detectors import AnalysisEngine
from falkor.logging_config import configure_logging, get_logger, LogContext
from falkor.config import load_config, FalkorConfig, ConfigError, generate_config_template, load_config_from_env
from falkor.validation import (
    ValidationError,
    validate_repository_path,
    validate_neo4j_uri,
    validate_neo4j_credentials,
    validate_neo4j_connection,
    validate_output_path,
    validate_file_size_limit,
    validate_batch_size,
    validate_retry_config,
)

console = Console()
logger = get_logger(__name__)

# Global config storage (loaded once per CLI invocation)
_config: FalkorConfig | None = None


def get_config() -> FalkorConfig:
    """Get loaded configuration."""
    global _config
    if _config is None:
        _config = FalkorConfig()  # Defaults
    return _config


@click.group()
@click.version_option(version="0.1.0")
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True),
    default=None,
    help="Path to config file (.falkorrc or falkor.toml)",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    default=None,
    help="Set logging level (overrides config file)",
)
@click.option(
    "--log-format",
    type=click.Choice(["json", "human"], case_sensitive=False),
    default=None,
    help="Log output format (overrides config file)",
)
@click.option(
    "--log-file",
    type=click.Path(),
    default=None,
    help="Write logs to file (overrides config file)",
)
@click.pass_context
def cli(ctx: click.Context, config: str | None, log_level: str | None, log_format: str | None, log_file: str | None) -> None:
    """Falkor - Graph-Powered Code Health Platform

    Configuration priority (highest to lowest):
    1. Command-line options
    2. Config file (--config, .falkorrc, falkor.toml)
    3. Environment variables
    4. Built-in defaults
    """
    global _config

    # Load configuration
    try:
        _config = load_config(config_file=config)
    except ConfigError as e:
        console.print(f"[yellow]⚠️  Config error: {e}[/yellow]")
        console.print("[dim]Using default configuration[/dim]\n")
        _config = FalkorConfig()

    # Configure logging (CLI options override config)
    final_log_level = log_level or _config.logging.level
    final_log_format = log_format or _config.logging.format
    final_log_file = log_file or _config.logging.file

    configure_logging(
        level=final_log_level,
        json_output=(final_log_format == "json"),
        log_file=final_log_file
    )

    # Store config in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj['config'] = _config


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option(
    "--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)"
)
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option(
    "--neo4j-password",
    default=None,
    help="Neo4j password (overrides config, prompts if not provided)",
)
@click.option(
    "--pattern",
    "-p",
    multiple=True,
    default=None,
    help="File patterns to analyze (overrides config)",
)
@click.option(
    "--follow-symlinks",
    is_flag=True,
    default=None,
    help="Follow symbolic links (overrides config)",
)
@click.option(
    "--max-file-size",
    type=float,
    default=None,
    help="Maximum file size in MB (overrides config)",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="Disable progress bars and reduce output",
)
@click.pass_context
def ingest(
    ctx: click.Context,
    repo_path: str,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    pattern: tuple | None,
    follow_symlinks: bool | None,
    max_file_size: float | None,
    quiet: bool,
) -> None:
    """Ingest a codebase into the knowledge graph with security validation.

    Security features:
    - Repository path validation and boundary checks
    - Symlink protection (disabled by default)
    - File size limits (10MB default)
    - Relative path storage (prevents system path exposure)
    """
    # Get config from context
    config: FalkorConfig = ctx.obj['config']

    # Validate inputs before execution
    try:
        # Validate repository path
        validated_repo_path = validate_repository_path(repo_path)

        # Apply config defaults (CLI options override config)
        final_neo4j_uri = neo4j_uri or config.neo4j.uri
        final_neo4j_user = neo4j_user or config.neo4j.user
        final_neo4j_password = neo4j_password or config.neo4j.password
        final_patterns = list(pattern) if pattern else config.ingestion.patterns
        final_follow_symlinks = follow_symlinks if follow_symlinks is not None else config.ingestion.follow_symlinks
        final_max_file_size = max_file_size if max_file_size is not None else config.ingestion.max_file_size_mb

        # Validate Neo4j URI
        final_neo4j_uri = validate_neo4j_uri(final_neo4j_uri)

        # Prompt for password if not provided
        if not final_neo4j_password:
            final_neo4j_password = click.prompt("Neo4j password", hide_input=True)

        # Validate credentials
        final_neo4j_user, final_neo4j_password = validate_neo4j_credentials(
            final_neo4j_user, final_neo4j_password
        )

        # Test Neo4j connection is reachable
        console.print("[dim]Checking Neo4j connectivity...[/dim]")
        validate_neo4j_connection(final_neo4j_uri, final_neo4j_user, final_neo4j_password)
        console.print("[green]✓[/green] Neo4j connection validated\n")

        # Validate file size limit
        final_max_file_size = validate_file_size_limit(final_max_file_size)

        # Validate batch size
        validated_batch_size = validate_batch_size(config.ingestion.batch_size)

        # Validate retry configuration
        validated_retries = validate_retry_config(
            config.neo4j.max_retries,
            config.neo4j.retry_backoff_factor,
            config.neo4j.retry_base_delay
        )

    except ValidationError as e:
        console.print(f"\n[red]❌ Validation Error:[/red] {e.message}")
        if e.suggestion:
            console.print(f"\n[yellow]{e.suggestion}[/yellow]")
        raise click.Abort()

    console.print(f"\n[bold cyan]🐉 Falkor Ingestion[/bold cyan]\n")
    console.print(f"Repository: {repo_path}")
    console.print(f"Patterns: {', '.join(final_patterns)}")
    console.print(f"Follow symlinks: {final_follow_symlinks}")
    console.print(f"Max file size: {final_max_file_size}MB\n")

    try:
        with LogContext(operation="ingest", repo_path=repo_path):
            logger.info("Starting ingestion")

            with Neo4jClient(
                final_neo4j_uri,
                final_neo4j_user,
                final_neo4j_password,
                max_retries=validated_retries[0],
                retry_backoff_factor=validated_retries[1],
                retry_base_delay=validated_retries[2],
            ) as db:
                pipeline = IngestionPipeline(
                    str(validated_repo_path),
                    db,
                    follow_symlinks=final_follow_symlinks,
                    max_file_size_mb=final_max_file_size,
                    batch_size=validated_batch_size
                )

                # Setup progress bar if not in quiet mode
                if not quiet:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        TimeRemainingColumn(),
                        console=console,
                    ) as progress:
                        # Create a task that will be updated by the callback
                        task_id = progress.add_task("[cyan]Ingesting files...", total=None)

                        def progress_callback(current: int, total: int, filename: str) -> None:
                            """Update progress bar with current file processing status."""
                            # Update task total if not set yet
                            if progress.tasks[task_id].total is None:
                                progress.update(task_id, total=total)

                            # Update progress with current file
                            progress.update(
                                task_id,
                                completed=current,
                                description=f"[cyan]Processing:[/cyan] {filename}"
                            )

                        pipeline.ingest(patterns=final_patterns, progress_callback=progress_callback)
                else:
                    # No progress bar in quiet mode
                    pipeline.ingest(patterns=final_patterns)

                # Show stats
                stats = db.get_stats()
                logger.info("Ingestion complete", extra={"stats": stats})

                table = Table(title="Ingestion Results")
                table.add_column("Metric", style="cyan")
                table.add_column("Count", style="green")

                for key, value in stats.items():
                    table.add_row(key.replace("_", " ").title(), str(value))

                console.print(table)

                # Show security info if files were skipped
                if pipeline.skipped_files:
                    console.print(
                        f"\n[yellow]⚠️  {len(pipeline.skipped_files)} files were skipped "
                        f"(see logs for details)[/yellow]"
                    )

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise click.Abort()
    except Exception as e:
        logger.exception("Unexpected error during ingestion")
        console.print(f"\n[red]❌ Unexpected error: {e}[/red]")
        raise


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option(
    "--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)"
)
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option(
    "--neo4j-password",
    default=None,
    help="Neo4j password (overrides config, prompts if not provided)",
)
@click.option(
    "--output", "-o", type=click.Path(), help="Output file for report"
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "html"], case_sensitive=False),
    default="json",
    help="Output format (json or html)",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="Disable progress indicators and reduce output",
)
@click.pass_context
def analyze(
    ctx: click.Context,
    repo_path: str,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    output: str | None,
    format: str,
    quiet: bool,
) -> None:
    """Analyze codebase health and generate report."""
    # Get config from context
    config: FalkorConfig = ctx.obj['config']

    # Validate inputs before execution
    try:
        # Validate repository path
        validated_repo_path = validate_repository_path(repo_path)

        # Apply config defaults (CLI options override config)
        final_neo4j_uri = neo4j_uri or config.neo4j.uri
        final_neo4j_user = neo4j_user or config.neo4j.user
        final_neo4j_password = neo4j_password or config.neo4j.password

        # Validate Neo4j URI
        final_neo4j_uri = validate_neo4j_uri(final_neo4j_uri)

        # Prompt for password if not provided
        if not final_neo4j_password:
            final_neo4j_password = click.prompt("Neo4j password", hide_input=True)

        # Validate credentials
        final_neo4j_user, final_neo4j_password = validate_neo4j_credentials(
            final_neo4j_user, final_neo4j_password
        )

        # Test Neo4j connection is reachable
        console.print("[dim]Checking Neo4j connectivity...[/dim]")
        validate_neo4j_connection(final_neo4j_uri, final_neo4j_user, final_neo4j_password)
        console.print("[green]✓[/green] Neo4j connection validated\n")

        # Validate output path if provided
        validated_output = None
        if output:
            validated_output = validate_output_path(output)

        # Validate retry configuration
        validated_retries = validate_retry_config(
            config.neo4j.max_retries,
            config.neo4j.retry_backoff_factor,
            config.neo4j.retry_base_delay
        )

    except ValidationError as e:
        console.print(f"\n[red]❌ Validation Error:[/red] {e.message}")
        if e.suggestion:
            console.print(f"\n[yellow]{e.suggestion}[/yellow]")
        raise click.Abort()

    console.print(f"\n[bold cyan]🐉 Falkor Analysis[/bold cyan]\n")

    try:
        with LogContext(operation="analyze", repo_path=repo_path):
            logger.info("Starting analysis")

            with Neo4jClient(
                final_neo4j_uri,
                final_neo4j_user,
                final_neo4j_password,
                max_retries=validated_retries[0],
                retry_backoff_factor=validated_retries[1],
                retry_base_delay=validated_retries[2],
            ) as db:
                # Convert detector config to dict for detectors
                detector_config_dict = asdict(config.detectors)
                engine = AnalysisEngine(db, detector_config=detector_config_dict)

                # Run analysis with progress indication
                if not quiet:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console,
                    ) as progress:
                        progress.add_task("[cyan]Running detectors and analyzing codebase...", total=None)
                        health = engine.analyze()
                else:
                    health = engine.analyze()

                logger.info("Analysis complete", extra={
                    "grade": health.grade,
                    "score": health.overall_score,
                    "total_findings": health.findings_summary.total
                })

                # Display results
                _display_health_report(health)

                # Save to file if requested
                if validated_output:
                    if format.lower() == "html":
                        from falkor.reporters import HTMLReporter
                        reporter = HTMLReporter(repo_path=validated_repo_path)
                        reporter.generate(health, validated_output)
                        logger.info(f"HTML report saved to {validated_output}")
                        console.print(f"\n✅ HTML report saved to {validated_output}")
                    else:  # JSON format
                        import json
                        with open(validated_output, "w") as f:
                            json.dump(health.to_dict(), f, indent=2)
                        logger.info(f"JSON report saved to {validated_output}")
                        console.print(f"\n✅ JSON report saved to {validated_output}")

    except Exception as e:
        logger.exception("Error during analysis")
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise


def _display_health_report(health) -> None:
    """Display health report in terminal with enhanced formatting."""
    from falkor.models import Severity

    # Severity color mapping
    SEVERITY_COLORS = {
        Severity.CRITICAL: "bright_red",
        Severity.HIGH: "red",
        Severity.MEDIUM: "yellow",
        Severity.LOW: "blue",
        Severity.INFO: "cyan",
    }

    SEVERITY_EMOJI = {
        Severity.CRITICAL: "🔴",
        Severity.HIGH: "🟠",
        Severity.MEDIUM: "🟡",
        Severity.LOW: "🔵",
        Severity.INFO: "ℹ️",
    }

    # Grade color mapping
    grade_colors = {"A": "green", "B": "cyan", "C": "yellow", "D": "bright_red", "F": "red"}
    grade_color = grade_colors.get(health.grade, "white")

    # Overall health panel with enhanced layout
    grade_text = Text()
    grade_text.append("Grade: ", style="bold")
    grade_text.append(health.grade, style=f"bold {grade_color}")
    grade_text.append(f"\nScore: {health.overall_score:.1f}/100", style="dim")

    # Add grade explanation
    grade_explanations = {
        "A": "Excellent - Code is well-structured and maintainable",
        "B": "Good - Minor improvements recommended",
        "C": "Fair - Several issues should be addressed",
        "D": "Poor - Significant refactoring needed",
        "F": "Critical - Major technical debt present"
    }
    grade_text.append(f"\n{grade_explanations.get(health.grade, '')}", style="italic dim")

    console.print(
        Panel(
            grade_text,
            title="🐉 Falkor Health Report",
            border_style=grade_color,
            box=box.DOUBLE,
            padding=(1, 2),
        )
    )

    # Category scores with enhanced visuals
    scores_table = Table(title="📊 Category Scores", box=box.ROUNDED, show_header=True, header_style="bold magenta")
    scores_table.add_column("Category", style="cyan", no_wrap=True)
    scores_table.add_column("Weight", style="dim", justify="center")
    scores_table.add_column("Score", style="bold", justify="right")
    scores_table.add_column("Progress", justify="center", no_wrap=True)
    scores_table.add_column("Status", justify="center")

    categories = [
        ("Graph Structure", "40%", health.structure_score),
        ("Code Quality", "30%", health.quality_score),
        ("Architecture Health", "30%", health.architecture_score),
    ]

    for name, weight, score in categories:
        # Enhanced progress bar with color
        bar_length = 20
        filled = int((score / 100) * bar_length)
        bar_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        progress_bar = f"[{bar_color}]{'█' * filled}{'░' * (bar_length - filled)}[/{bar_color}]"

        # Score color based on value
        score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        score_text = f"[{score_color}]{score:.1f}/100[/{score_color}]"

        # Status emoji
        status = "✅" if score >= 80 else "⚠️" if score >= 60 else "❌"

        scores_table.add_row(name, weight, score_text, progress_bar, status)

    console.print(scores_table)

    # Key metrics with better organization
    m = health.metrics
    metrics_table = Table(title="📈 Key Metrics", box=box.ROUNDED, show_header=True, header_style="bold cyan")
    metrics_table.add_column("Metric", style="cyan", no_wrap=True)
    metrics_table.add_column("Value", style="bold", justify="right")
    metrics_table.add_column("Assessment", justify="center")

    # Codebase size metrics
    metrics_table.add_row("📁 Total Files", str(m.total_files), "")
    metrics_table.add_row("🏛️  Classes", str(m.total_classes), "")
    metrics_table.add_row("⚙️  Functions", str(m.total_functions), "")
    if m.total_loc > 0:
        metrics_table.add_row("📝 Lines of Code", f"{m.total_loc:,}", "")

    # Separator
    metrics_table.add_row("", "", "")

    # Quality metrics with color-coded assessments
    modularity_status = "[green]Excellent[/green]" if m.modularity > 0.6 else "[yellow]Moderate[/yellow]" if m.modularity > 0.3 else "[red]Poor[/red]"
    metrics_table.add_row("🔗 Modularity", f"{m.modularity:.2f}", modularity_status)

    if m.avg_coupling is not None:
        coupling_status = "[green]Good[/green]" if m.avg_coupling < 3 else "[yellow]Moderate[/yellow]" if m.avg_coupling < 5 else "[red]High[/red]"
        metrics_table.add_row("🔄 Avg Coupling", f"{m.avg_coupling:.1f}", coupling_status)

    circular_deps_status = "[green]✓ None[/green]" if m.circular_dependencies == 0 else f"[red]⚠️  {m.circular_dependencies}[/red]"
    metrics_table.add_row("🔁 Circular Deps", str(m.circular_dependencies), circular_deps_status)

    god_class_status = "[green]✓ None[/green]" if m.god_class_count == 0 else f"[red]⚠️  {m.god_class_count}[/red]"
    metrics_table.add_row("👹 God Classes", str(m.god_class_count), god_class_status)

    if m.dead_code_percentage > 0:
        dead_code_status = "[green]✓ Low[/green]" if m.dead_code_percentage < 5 else "[yellow]⚠️  Moderate[/yellow]" if m.dead_code_percentage < 10 else "[red]❌ High[/red]"
        metrics_table.add_row("💀 Dead Code", f"{m.dead_code_percentage:.1f}%", dead_code_status)

    console.print(metrics_table)

    # Findings summary with severity colors
    fs = health.findings_summary
    if fs.total > 0:
        findings_table = Table(
            title=f"🔍 Findings Summary ({fs.total} total)",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold red"
        )
        findings_table.add_column("Severity", style="bold", no_wrap=True)
        findings_table.add_column("Count", style="bold", justify="right")
        findings_table.add_column("Impact", justify="center")

        severity_data = [
            (Severity.CRITICAL, fs.critical, "Must fix immediately"),
            (Severity.HIGH, fs.high, "Should fix soon"),
            (Severity.MEDIUM, fs.medium, "Plan to address"),
            (Severity.LOW, fs.low, "Consider fixing"),
            (Severity.INFO, fs.info, "Informational"),
        ]

        for severity, count, impact in severity_data:
            if count > 0:
                color = SEVERITY_COLORS[severity]
                emoji = SEVERITY_EMOJI[severity]
                severity_text = f"{emoji} [{color}]{severity.value.title()}[/{color}]"
                count_text = f"[{color}]{count}[/{color}]"
                findings_table.add_row(severity_text, count_text, f"[dim]{impact}[/dim]")

        console.print(findings_table)

        # Detailed findings tree view
        if health.findings:
            console.print("\n[bold cyan]📋 Detailed Findings[/bold cyan]\n")
            _display_findings_tree(health.findings[:10], SEVERITY_COLORS, SEVERITY_EMOJI)

            if len(health.findings) > 10:
                console.print(f"\n[dim]... and {len(health.findings) - 10} more findings[/dim]")
                console.print("[dim]Use --output to save full report to JSON file[/dim]")


def _display_findings_tree(findings, severity_colors, severity_emoji):
    """Display findings in a tree structure grouped by detector."""
    from collections import defaultdict

    # Group findings by detector
    by_detector = defaultdict(list)
    for finding in findings:
        by_detector[finding.detector].append(finding)

    # Create tree for each detector
    for detector, detector_findings in sorted(by_detector.items()):
        tree = Tree(f"[bold cyan]{detector}[/bold cyan]")

        for finding in detector_findings:
            color = severity_colors[finding.severity]
            emoji = severity_emoji[finding.severity]

            # Create finding branch
            severity_label = f"{emoji} [{color}]{finding.severity.value.upper()}[/{color}]"
            finding_text = f"{severity_label}: {finding.title}"
            finding_branch = tree.add(finding_text)

            # Add description
            if finding.description:
                finding_branch.add(f"[dim]{finding.description}[/dim]")

            # Add affected files
            if finding.affected_files:
                files_text = ", ".join(finding.affected_files[:3])
                if len(finding.affected_files) > 3:
                    files_text += f" [dim](+{len(finding.affected_files) - 3} more)[/dim]"
                finding_branch.add(f"[yellow]Files:[/yellow] {files_text}")

            # Add suggested fix if available
            if finding.suggested_fix:
                fix_branch = finding_branch.add("[green]💡 Suggested Fix:[/green]")
                # Limit fix text length for display
                fix_text = finding.suggested_fix
                if len(fix_text) > 200:
                    fix_text = fix_text[:200] + "..."
                fix_branch.add(f"[dim]{fix_text}[/dim]")

        console.print(tree)
        console.print()  # Add spacing between detectors


@cli.command()
@click.option(
    "--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)"
)
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option(
    "--neo4j-password",
    default=None,
    help="Neo4j password (overrides config, prompts if not provided)",
)
@click.pass_context
def validate(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
) -> None:
    """Validate configuration and connectivity without running operations.

    Checks:
    - Configuration file validity (if present)
    - Neo4j connection URI format
    - Neo4j credentials
    - Neo4j connectivity (database is reachable)
    - All required settings are present

    Exits with non-zero code if any validation fails.
    """
    config: FalkorConfig = ctx.obj['config']

    console.print("\n[bold cyan]🐉 Falkor Configuration Validation[/bold cyan]\n")

    validation_results = []
    all_passed = True

    # 1. Validate configuration file
    console.print("[dim]Checking configuration file...[/dim]")
    try:
        # Config is already loaded in the parent command
        validation_results.append(("Configuration file", "✓ Valid", "green"))
        console.print("[green]✓[/green] Configuration file valid\n")
    except Exception as e:
        validation_results.append(("Configuration file", f"✗ {e}", "red"))
        console.print(f"[red]✗[/red] Configuration file error: {e}\n")
        all_passed = False

    # 2. Validate Neo4j URI
    console.print("[dim]Validating Neo4j URI...[/dim]")
    final_neo4j_uri = neo4j_uri or config.neo4j.uri
    try:
        validated_uri = validate_neo4j_uri(final_neo4j_uri)
        validation_results.append(("Neo4j URI", f"✓ {validated_uri}", "green"))
        console.print(f"[green]✓[/green] Neo4j URI valid: {validated_uri}\n")
    except ValidationError as e:
        validation_results.append(("Neo4j URI", f"✗ {e.message}", "red"))
        console.print(f"[red]✗[/red] {e.message}")
        if e.suggestion:
            console.print(f"[yellow]💡 {e.suggestion}[/yellow]\n")
        all_passed = False
        # Can't proceed without valid URI
        _print_validation_summary(validation_results, all_passed)
        raise click.Abort()

    # 3. Validate Neo4j credentials
    console.print("[dim]Validating Neo4j credentials...[/dim]")
    final_neo4j_user = neo4j_user or config.neo4j.user
    final_neo4j_password = neo4j_password or config.neo4j.password

    # Prompt for password if not provided
    if not final_neo4j_password:
        final_neo4j_password = click.prompt("Neo4j password", hide_input=True)

    try:
        validated_user, validated_password = validate_neo4j_credentials(
            final_neo4j_user, final_neo4j_password
        )
        validation_results.append(("Neo4j credentials", f"✓ User: {validated_user}", "green"))
        console.print(f"[green]✓[/green] Neo4j credentials valid (user: {validated_user})\n")
    except ValidationError as e:
        validation_results.append(("Neo4j credentials", f"✗ {e.message}", "red"))
        console.print(f"[red]✗[/red] {e.message}")
        if e.suggestion:
            console.print(f"[yellow]💡 {e.suggestion}[/yellow]\n")
        all_passed = False
        _print_validation_summary(validation_results, all_passed)
        raise click.Abort()

    # 4. Test Neo4j connectivity
    console.print("[dim]Testing Neo4j connectivity...[/dim]")
    try:
        validate_neo4j_connection(validated_uri, validated_user, validated_password)
        validation_results.append(("Neo4j connectivity", "✓ Connected successfully", "green"))
        console.print("[green]✓[/green] Neo4j connection successful\n")
    except ValidationError as e:
        validation_results.append(("Neo4j connectivity", f"✗ {e.message}", "red"))
        console.print(f"[red]✗[/red] {e.message}")
        if e.suggestion:
            console.print(f"[yellow]💡 {e.suggestion}[/yellow]\n")
        all_passed = False

    # 5. Validate ingestion settings
    console.print("[dim]Validating ingestion settings...[/dim]")
    try:
        validate_file_size_limit(config.ingestion.max_file_size_mb)
        validate_batch_size(config.ingestion.batch_size)
        validation_results.append(("Ingestion settings", "✓ Valid", "green"))
        console.print("[green]✓[/green] Ingestion settings valid\n")
    except ValidationError as e:
        validation_results.append(("Ingestion settings", f"✗ {e.message}", "red"))
        console.print(f"[red]✗[/red] {e.message}")
        if e.suggestion:
            console.print(f"[yellow]💡 {e.suggestion}[/yellow]\n")
        all_passed = False

    # 6. Validate retry configuration
    console.print("[dim]Validating retry configuration...[/dim]")
    try:
        validate_retry_config(
            config.neo4j.max_retries,
            config.neo4j.retry_backoff_factor,
            config.neo4j.retry_base_delay
        )
        validation_results.append(("Retry configuration", "✓ Valid", "green"))
        console.print("[green]✓[/green] Retry configuration valid\n")
    except ValidationError as e:
        validation_results.append(("Retry configuration", f"✗ {e.message}", "red"))
        console.print(f"[red]✗[/red] {e.message}")
        if e.suggestion:
            console.print(f"[yellow]💡 {e.suggestion}[/yellow]\n")
        all_passed = False

    # Print summary
    _print_validation_summary(validation_results, all_passed)

    if not all_passed:
        raise click.Abort()


def _print_validation_summary(results: list, all_passed: bool) -> None:
    """Print validation summary table."""
    table = Table(title="Validation Summary")
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="white")

    for check, result, color in results:
        table.add_row(check, f"[{color}]{result}[/{color}]")

    console.print(table)

    if all_passed:
        console.print("\n[bold green]✓ All validations passed![/bold green]")
        console.print("[dim]Your Falkor configuration is ready to use.[/dim]\n")
    else:
        console.print("\n[bold red]✗ Some validations failed[/bold red]")
        console.print("[dim]Fix the issues above and try again.[/dim]\n")


@cli.command()
@click.option(
    "--format",
    "-f",
    type=click.Choice(["yaml", "json", "table"], case_sensitive=False),
    default="table",
    help="Output format (default: table)",
)
@click.pass_context
def show_config(ctx: click.Context, format: str) -> None:
    """Display effective configuration from all sources.

    Shows the final configuration after applying the priority chain:
    1. Command-line arguments (highest priority)
    2. Environment variables (FALKOR_*)
    3. Config file (.falkorrc, falkor.toml)
    4. Built-in defaults (lowest priority)

    Use --format to control output format:
    - table: Pretty-printed table (default)
    - json: JSON format
    - yaml: YAML format (requires PyYAML)
    """
    console.print("\n[bold cyan]🐉 Falkor Configuration[/bold cyan]\n")

    # Get config from context
    config: FalkorConfig = ctx.obj['config']

    if format == "json":
        import json
        console.print(json.dumps(config.to_dict(), indent=2))

    elif format == "yaml":
        try:
            import yaml
            console.print(yaml.dump(config.to_dict(), default_flow_style=False, sort_keys=False))
        except ImportError:
            console.print("[red]Error: PyYAML not installed. Use 'pip install pyyaml'[/red]")
            raise click.Abort()

    else:  # table format
        # Neo4j configuration
        neo4j_table = Table(title="Neo4j Configuration")
        neo4j_table.add_column("Setting", style="cyan")
        neo4j_table.add_column("Value", style="green")
        neo4j_table.add_row("URI", config.neo4j.uri)
        neo4j_table.add_row("User", config.neo4j.user)
        neo4j_table.add_row("Password", "***" if config.neo4j.password else "[dim]not set[/dim]")
        console.print(neo4j_table)

        # Ingestion configuration
        ingestion_table = Table(title="Ingestion Configuration")
        ingestion_table.add_column("Setting", style="cyan")
        ingestion_table.add_column("Value", style="green")
        ingestion_table.add_row("Patterns", ", ".join(config.ingestion.patterns))
        ingestion_table.add_row("Follow Symlinks", str(config.ingestion.follow_symlinks))
        ingestion_table.add_row("Max File Size (MB)", str(config.ingestion.max_file_size_mb))
        ingestion_table.add_row("Batch Size", str(config.ingestion.batch_size))
        console.print(ingestion_table)

        # Analysis configuration
        analysis_table = Table(title="Analysis Configuration")
        analysis_table.add_column("Setting", style="cyan")
        analysis_table.add_column("Value", style="green")
        analysis_table.add_row("Min Modularity", str(config.analysis.min_modularity))
        analysis_table.add_row("Max Coupling", str(config.analysis.max_coupling))
        console.print(analysis_table)

        # Logging configuration
        logging_table = Table(title="Logging Configuration")
        logging_table.add_column("Setting", style="cyan")
        logging_table.add_column("Value", style="green")
        logging_table.add_row("Level", config.logging.level)
        logging_table.add_row("Format", config.logging.format)
        logging_table.add_row("File", config.logging.file or "[dim]none[/dim]")
        console.print(logging_table)

        # Show configuration sources
        console.print("\n[bold]Configuration Priority:[/bold]")
        console.print("  1. Command-line arguments (highest)")
        console.print("  2. Environment variables (FALKOR_*)")
        console.print("  3. Config file (.falkorrc, falkor.toml)")
        console.print("  4. Built-in defaults (lowest)\n")


@cli.command()
@click.option(
    "--format",
    "-f",
    type=click.Choice(["yaml", "json", "toml"], case_sensitive=False),
    default="yaml",
    help="Config file format (default: yaml)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output file path (default: .falkorrc for yaml/json, falkor.toml for toml)",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing config file",
)
def init(format: str, output: str | None, force: bool) -> None:
    """Initialize a new Falkor configuration file.

    Creates a config file template with default values and comments.

    Examples:
        falkor init                    # Create .falkorrc (YAML)
        falkor init -f json            # Create .falkorrc (JSON)
        falkor init -f toml            # Create falkor.toml
        falkor init -o myconfig.yaml   # Custom output path
    """
    console.print("\n[bold cyan]🐉 Falkor Configuration Init[/bold cyan]\n")

    # Determine output file
    if output:
        output_path = Path(output)
    else:
        if format == "toml":
            output_path = Path("falkor.toml")
        else:
            output_path = Path(".falkorrc")

    # Check if file exists
    if output_path.exists() and not force:
        console.print(f"[yellow]⚠️  Config file already exists: {output_path}[/yellow]")
        console.print("[dim]Use --force to overwrite[/dim]")
        raise click.Abort()

    try:
        # Generate template
        template = generate_config_template(format=format)

        # Write to file
        output_path.write_text(template)

        console.print(f"[green]✓ Created config file: {output_path}[/green]")
        console.print(f"\n[dim]Edit the file to customize your configuration.[/dim]")
        console.print(f"[dim]Environment variables can be referenced using ${{VAR_NAME}} syntax.[/dim]\n")

        # Show snippet
        lines = template.split("\n")[:15]  # First 15 lines
        console.print("[bold]Preview:[/bold]")
        for line in lines:
            console.print(f"[dim]{line}[/dim]")
        if len(template.split("\n")) > 15:
            console.print("[dim]...[/dim]\n")

    except ConfigError as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]❌ Unexpected error: {e}[/red]")
        raise


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
