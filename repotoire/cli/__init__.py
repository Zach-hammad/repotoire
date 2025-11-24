"""Command-line interface for Repotoire."""

import click
from dataclasses import asdict
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.tree import Tree
from rich.text import Text
from rich.prompt import Confirm
from rich import box

from repotoire.pipeline import IngestionPipeline
from repotoire.graph import Neo4jClient
from repotoire.detectors import AnalysisEngine
from repotoire.migrations import MigrationManager, MigrationError
from repotoire.logging_config import configure_logging, get_logger, LogContext
from repotoire.config import load_config, FalkorConfig, ConfigError, generate_config_template
from repotoire.models import SecretsPolicy
from repotoire.validation import (
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


def _extract_git_info(repo_path: Path) -> dict[str, str | None]:
    """Extract git branch and commit SHA from repository.

    Args:
        repo_path: Path to git repository

    Returns:
        Dictionary with 'branch' and 'commit_sha' keys
    """
    import subprocess

    git_info = {"branch": None, "commit_sha": None}

    try:
        # Get current branch
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_info["branch"] = result.stdout.strip()

        # Get commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_info["commit_sha"] = result.stdout.strip()

    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or timeout - return None values
        pass

    return git_info


def _record_metrics_to_timescale(
    health,
    repo_path: Path,
    config: FalkorConfig,
    quiet: bool
) -> None:
    """Record analysis metrics to TimescaleDB for historical tracking.

    Args:
        health: CodebaseHealth object from analysis
        repo_path: Path to analyzed repository
        config: Loaded configuration
        quiet: Whether to suppress output
    """
    try:
        # Check if TimescaleDB is enabled in config
        if not config.timescale.enabled:
            console.print("\n[yellow]⚠️  TimescaleDB tracking requested but not enabled in config[/yellow]")
            console.print("[dim]Set timescale.enabled = true in your config file[/dim]")
            return

        # Check for connection string
        if not config.timescale.connection_string:
            console.print("\n[yellow]⚠️  TimescaleDB connection string not configured[/yellow]")
            console.print("[dim]Set timescale.connection_string in config or REPOTOIRE_TIMESCALE_URI env var[/dim]")
            return

        if not quiet:
            console.print("\n[dim]Recording metrics to TimescaleDB...[/dim]")

        # Import TimescaleDB components
        from repotoire.historical import TimescaleClient, MetricsCollector

        # Extract git information
        git_info = _extract_git_info(repo_path)

        # Extract metrics from health object
        collector = MetricsCollector()
        metrics = collector.extract_metrics(health)

        # Record to TimescaleDB
        with TimescaleClient(config.timescale.connection_string) as client:
            client.record_metrics(
                metrics=metrics,
                repository=str(repo_path),
                branch=git_info["branch"] or "unknown",
                commit_sha=git_info["commit_sha"],
            )

        logger.info(
            "Metrics recorded to TimescaleDB",
            extra={
                "repository": str(repo_path),
                "branch": git_info["branch"],
                "commit_sha": git_info["commit_sha"][:8] if git_info["commit_sha"] else None,
            }
        )

        if not quiet:
            console.print("[green]✓[/green] Metrics recorded to TimescaleDB")
            if git_info["branch"]:
                console.print(f"[dim]  Branch: {git_info['branch']}[/dim]")
            if git_info["commit_sha"]:
                console.print(f"[dim]  Commit: {git_info['commit_sha'][:8]}[/dim]")

    except ImportError:
        console.print("\n[yellow]⚠️  TimescaleDB support not installed[/yellow]")
        console.print("[dim]Install with: pip install repotoire[timescale][/dim]")
        logger.warning("TimescaleDB support not installed (missing psycopg2)")

    except Exception as e:
        logger.exception("Failed to record metrics to TimescaleDB")
        console.print(f"\n[red]⚠️  Failed to record metrics: {e}[/red]")
        console.print("[dim]Analysis results are still available[/dim]")


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
    help="Path to config file (.reporc or falkor.toml)",
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
    """Repotoire - Graph-Powered Code Health Platform

    Configuration priority (highest to lowest):
    1. Command-line options
    2. Config file (--config, .reporc, falkor.toml)
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
    "--secrets-policy",
    type=click.Choice(["redact", "block", "warn", "fail"], case_sensitive=False),
    default=None,
    help="Policy for handling detected secrets (overrides config, default: redact)",
)
@click.option(
    "--incremental/--no-incremental",
    default=True,
    help="Use incremental ingestion (skip unchanged files, default: enabled)",
)
@click.option(
    "--force-full",
    is_flag=True,
    default=False,
    help="Force full re-ingestion (ignore file hashes)",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="Disable progress bars and reduce output",
)
@click.option(
    "--generate-clues",
    is_flag=True,
    default=False,
    help="Generate AI-powered semantic clues (requires spaCy)",
)
@click.option(
    "--generate-embeddings",
    is_flag=True,
    default=False,
    help="Generate vector embeddings for RAG (requires OpenAI API key)",
)
@click.option(
    "--batch-size",
    type=int,
    default=None,
    help="Number of entities to batch before loading to graph (overrides config, default: 100)",
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
    secrets_policy: str | None,
    incremental: bool,
    force_full: bool,
    quiet: bool,
    generate_clues: bool,
    generate_embeddings: bool,
    batch_size: int | None,
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
        final_secrets_policy_str = secrets_policy if secrets_policy is not None else config.secrets.policy
        final_batch_size = batch_size if batch_size is not None else config.ingestion.batch_size

        # Convert secrets policy string to enum
        final_secrets_policy = SecretsPolicy(final_secrets_policy_str)

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
        validated_batch_size = validate_batch_size(final_batch_size)

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

    console.print(f"\n[bold cyan]🎼 Repotoire Ingestion[/bold cyan]\n")
    console.print(f"Repository: {repo_path}")
    console.print(f"Patterns: {', '.join(final_patterns)}")
    console.print(f"Follow symlinks: {final_follow_symlinks}")
    console.print(f"Max file size: {final_max_file_size}MB")
    if generate_clues:
        console.print(f"[cyan]✨ AI Clue Generation: Enabled (spaCy)[/cyan]")
    if generate_embeddings:
        console.print(f"[cyan]🔮 Vector Embeddings: Enabled (OpenAI)[/cyan]")
    console.print()

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
                # Clear database if force-full is requested
                if force_full:
                    console.print("[yellow]⚠️  Force-full mode: Clearing existing graph...[/yellow]")
                    db.clear_graph()
                    console.print("[green]✓ Database cleared[/green]\n")

                pipeline = IngestionPipeline(
                    str(validated_repo_path),
                    db,
                    follow_symlinks=final_follow_symlinks,
                    max_file_size_mb=final_max_file_size,
                    batch_size=validated_batch_size,
                    secrets_policy=final_secrets_policy,
                    generate_clues=generate_clues,
                    generate_embeddings=generate_embeddings
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

                        pipeline.ingest(
                            patterns=final_patterns,
                            incremental=incremental and not force_full,
                            progress_callback=progress_callback
                        )
                else:
                    # No progress bar in quiet mode
                    pipeline.ingest(
                        patterns=final_patterns,
                        incremental=incremental and not force_full
                    )

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
@click.option(
    "--track-metrics",
    is_flag=True,
    default=False,
    help="Record metrics to TimescaleDB for historical tracking",
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
    track_metrics: bool,
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

    console.print(f"\n[bold cyan]🎼 Repotoire Analysis[/bold cyan]\n")

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
                        from repotoire.reporters import HTMLReporter
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

                # Record metrics to TimescaleDB if enabled
                if track_metrics or config.timescale.auto_track:
                    _record_metrics_to_timescale(
                        health=health,
                        repo_path=validated_repo_path,
                        config=config,
                        quiet=quiet
                    )

    except Exception as e:
        logger.exception("Error during analysis")
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise


def _display_health_report(health) -> None:
    """Display health report in terminal with enhanced formatting."""
    from repotoire.models import Severity

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
            title="🎼 Repotoire Health Report",
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

    console.print("\n[bold cyan]🎼 Repotoire Configuration Validation[/bold cyan]\n")

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
        console.print("[dim]Your Repotoire configuration is ready to use.[/dim]\n")
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
    3. Config file (.reporc, falkor.toml)
    4. Built-in defaults (lowest priority)

    Use --format to control output format:
    - table: Pretty-printed table (default)
    - json: JSON format
    - yaml: YAML format (requires PyYAML)
    """
    console.print("\n[bold cyan]🎼 Repotoire Configuration[/bold cyan]\n")

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
        console.print("  3. Config file (.reporc, falkor.toml)")
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
    help="Output file path (default: .reporc for yaml/json, falkor.toml for toml)",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing config file",
)
def init(format: str, output: str | None, force: bool) -> None:
    """Initialize a new Repotoire configuration file.

    Creates a config file template with default values and comments.

    Examples:
        falkor init                    # Create .reporc (YAML)
        falkor init -f json            # Create .reporc (JSON)
        falkor init -f toml            # Create falkor.toml
        falkor init -o myconfig.yaml   # Custom output path
    """
    console.print("\n[bold cyan]🎼 Repotoire Configuration Init[/bold cyan]\n")

    # Determine output file
    if output:
        output_path = Path(output)
    else:
        if format == "toml":
            output_path = Path("falkor.toml")
        else:
            output_path = Path(".reporc")

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


@cli.group()
def migrate() -> None:
    """Manage database schema migrations.

    Schema migrations allow you to safely evolve the Neo4j database schema
    over time with version tracking and rollback capabilities.

    Examples:
        falkor migrate status              # Show current migration state
        falkor migrate up                  # Apply pending migrations
        falkor migrate down --to-version 1 # Rollback to version 1
    """
    pass


@migrate.command()
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
def status(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
) -> None:
    """Show current migration status and pending migrations."""
    config: FalkorConfig = ctx.obj['config']

    # Validate and get credentials
    try:
        final_neo4j_uri = validate_neo4j_uri(neo4j_uri or config.neo4j.uri)
        final_neo4j_user = neo4j_user or config.neo4j.user
        final_neo4j_password = neo4j_password or config.neo4j.password

        if not final_neo4j_password:
            final_neo4j_password = click.prompt("Neo4j password", hide_input=True)

        final_neo4j_user, final_neo4j_password = validate_neo4j_credentials(
            final_neo4j_user, final_neo4j_password
        )

    except ValidationError as e:
        console.print(f"\n[red]❌ Validation Error:[/red] {e.message}")
        if e.suggestion:
            console.print(f"\n[yellow]{e.suggestion}[/yellow]")
        raise click.Abort()

    console.print(f"\n[bold cyan]🎼 Repotoire Migration Status[/bold cyan]\n")

    try:
        with Neo4jClient(final_neo4j_uri, final_neo4j_user, final_neo4j_password) as db:
            manager = MigrationManager(db)
            status_info = manager.status()

            # Current version panel
            version_text = Text()
            version_text.append("Current Version: ", style="bold")
            version_text.append(str(status_info["current_version"]), style="bold cyan")
            version_text.append(f"\nAvailable Migrations: {status_info['available_migrations']}", style="dim")
            version_text.append(f"\nPending Migrations: {status_info['pending_migrations']}", style="dim")

            console.print(
                Panel(
                    version_text,
                    title="Schema Version",
                    border_style="cyan",
                    box=box.ROUNDED,
                    padding=(1, 2),
                )
            )

            # Pending migrations table
            if status_info["pending"]:
                pending_table = Table(title="⏳ Pending Migrations", box=box.ROUNDED)
                pending_table.add_column("Version", style="cyan", justify="center")
                pending_table.add_column("Description", style="white")

                for migration in status_info["pending"]:
                    pending_table.add_row(
                        str(migration["version"]),
                        migration["description"]
                    )

                console.print(pending_table)
                console.print(f"\n[yellow]Run 'falkor migrate up' to apply pending migrations[/yellow]\n")
            else:
                console.print("[green]✓ Database schema is up to date[/green]\n")

            # Migration history table
            if status_info["history"]:
                history_table = Table(title="📜 Migration History", box=box.ROUNDED)
                history_table.add_column("Version", style="cyan", justify="center")
                history_table.add_column("Description", style="white")
                history_table.add_column("Applied At", style="dim")

                for record in status_info["history"]:
                    history_table.add_row(
                        str(record["version"]),
                        record["description"],
                        record["applied_at"][:19] if record["applied_at"] else "N/A"
                    )

                console.print(history_table)

    except MigrationError as e:
        console.print(f"\n[red]❌ Migration Error:[/red] {e}")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]❌ Unexpected error:[/red] {e}")
        raise


@migrate.command()
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
    "--to-version",
    type=int,
    default=None,
    help="Target version to migrate to (default: latest)",
)
@click.pass_context
def up(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    to_version: int | None,
) -> None:
    """Apply pending migrations to upgrade schema."""
    config: FalkorConfig = ctx.obj['config']

    # Validate and get credentials
    try:
        final_neo4j_uri = validate_neo4j_uri(neo4j_uri or config.neo4j.uri)
        final_neo4j_user = neo4j_user or config.neo4j.user
        final_neo4j_password = neo4j_password or config.neo4j.password

        if not final_neo4j_password:
            final_neo4j_password = click.prompt("Neo4j password", hide_input=True)

        final_neo4j_user, final_neo4j_password = validate_neo4j_credentials(
            final_neo4j_user, final_neo4j_password
        )

    except ValidationError as e:
        console.print(f"\n[red]❌ Validation Error:[/red] {e.message}")
        if e.suggestion:
            console.print(f"\n[yellow]{e.suggestion}[/yellow]")
        raise click.Abort()

    console.print(f"\n[bold cyan]🎼 Repotoire Migration: Upgrading Schema[/bold cyan]\n")

    try:
        with Neo4jClient(final_neo4j_uri, final_neo4j_user, final_neo4j_password) as db:
            manager = MigrationManager(db)

            # Show current state
            current = manager.get_current_version()
            console.print(f"Current version: [cyan]{current}[/cyan]")

            if to_version:
                console.print(f"Target version: [cyan]{to_version}[/cyan]\n")
            else:
                available = max(manager.migrations.keys()) if manager.migrations else 0
                console.print(f"Target version: [cyan]{available}[/cyan] (latest)\n")

            # Apply migrations
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task("[cyan]Applying migrations...", total=None)
                manager.migrate(target_version=to_version)

            console.print(f"\n[green]✓ Schema migration complete[/green]")

            # Show new version
            new_version = manager.get_current_version()
            console.print(f"New version: [bold cyan]{new_version}[/bold cyan]\n")

    except MigrationError as e:
        console.print(f"\n[red]❌ Migration Error:[/red] {e}")
        console.print("[yellow]⚠️  Schema may be in an inconsistent state[/yellow]")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]❌ Unexpected error:[/red] {e}")
        raise


@migrate.command()
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
    "--to-version",
    type=int,
    required=True,
    help="Target version to rollback to",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt",
)
@click.pass_context
def down(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    to_version: int,
    force: bool,
) -> None:
    """Rollback migrations to a previous version.

    WARNING: This operation may result in data loss. Use with caution!
    """
    config: FalkorConfig = ctx.obj['config']

    # Validate and get credentials
    try:
        final_neo4j_uri = validate_neo4j_uri(neo4j_uri or config.neo4j.uri)
        final_neo4j_user = neo4j_user or config.neo4j.user
        final_neo4j_password = neo4j_password or config.neo4j.password

        if not final_neo4j_password:
            final_neo4j_password = click.prompt("Neo4j password", hide_input=True)

        final_neo4j_user, final_neo4j_password = validate_neo4j_credentials(
            final_neo4j_user, final_neo4j_password
        )

    except ValidationError as e:
        console.print(f"\n[red]❌ Validation Error:[/red] {e.message}")
        if e.suggestion:
            console.print(f"\n[yellow]{e.suggestion}[/yellow]")
        raise click.Abort()

    console.print(f"\n[bold red]⚠️  Falkor Migration: Rollback Schema[/bold red]\n")

    try:
        with Neo4jClient(final_neo4j_uri, final_neo4j_user, final_neo4j_password) as db:
            manager = MigrationManager(db)

            # Show current state
            current = manager.get_current_version()
            console.print(f"Current version: [cyan]{current}[/cyan]")
            console.print(f"Target version: [cyan]{to_version}[/cyan]\n")

            if to_version >= current:
                console.print(f"[yellow]⚠️  Target version {to_version} is not earlier than current version {current}[/yellow]")
                console.print("[dim]Use 'falkor migrate up' to upgrade schema[/dim]")
                return

            # Confirm rollback
            if not force:
                console.print("[yellow]⚠️  WARNING: Rolling back migrations may result in data loss![/yellow]")
                confirm = click.confirm(f"Are you sure you want to rollback to version {to_version}?", default=False)
                if not confirm:
                    console.print("\n[dim]Rollback cancelled[/dim]")
                    return

            # Rollback migrations
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task("[red]Rolling back migrations...", total=None)
                manager.rollback(target_version=to_version)

            console.print(f"\n[green]✓ Schema rollback complete[/green]")

            # Show new version
            new_version = manager.get_current_version()
            console.print(f"New version: [bold cyan]{new_version}[/bold cyan]\n")

    except MigrationError as e:
        console.print(f"\n[red]❌ Migration Error:[/red] {e}")
        console.print("[yellow]⚠️  Schema may be in an inconsistent state[/yellow]")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]❌ Unexpected error:[/red] {e}")
        raise


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI")
@click.option("--neo4j-user", default=None, help="Neo4j username")
@click.option("--neo4j-password", default=None, help="Neo4j password")
@click.option("--window", type=int, default=90, help="Time window in days (default: 90)")
@click.option("--min-churn", type=int, default=5, help="Minimum modifications to qualify as hotspot (default: 5)")
@click.pass_context
def hotspots(ctx, repo_path: str, neo4j_uri, neo4j_user, neo4j_password, window: int, min_churn: int) -> None:
    """Find code hotspots with high churn and complexity.

    Analyzes Git history to find files with:
    - High modification frequency (churn)
    - Increasing complexity or coupling
    - High risk scores requiring attention

    Example:
        falkor hotspots /path/to/repo --window 90 --min-churn 5
    """
    config = ctx.obj['config']

    with console.status(f"[bold green]Finding code hotspots in last {window} days...", spinner="dots"):
        try:
            # Get Neo4j connection details
            uri = neo4j_uri or config.neo4j.uri
            user = neo4j_user or config.neo4j.user
            password = neo4j_password or config.neo4j.password or click.prompt("Neo4j password", hide_input=True)

            # Connect to Neo4j
            client = Neo4jClient(uri=uri, username=user, password=password)

            # Create temporal metrics analyzer
            from repotoire.detectors.temporal_metrics import TemporalMetrics
            analyzer = TemporalMetrics(client)

            # Find hotspots
            hotspots_list = analyzer.find_code_hotspots(window_days=window, min_churn=min_churn)

            if not hotspots_list:
                console.print(f"\n[green]✓ No code hotspots found in the last {window} days![/green]")
                console.print(f"[dim]This means no files have >{min_churn} modifications with increasing complexity[/dim]\n")
                return

            # Display hotspots table
            console.print(f"\n[bold red]🔥 Code Hotspots[/bold red] (Last {window} days)\n")

            table = Table(
                title=f"{len(hotspots_list)} files need attention",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold red"
            )
            table.add_column("File", style="yellow", no_wrap=False)
            table.add_column("Churn", justify="right", style="cyan")
            table.add_column("Risk Score", justify="right", style="red")
            table.add_column("Top Author", style="dim")

            for hotspot in hotspots_list[:20]:  # Top 20
                risk_indicator = "🔥" * min(int(hotspot.risk_score / 10), 5)
                table.add_row(
                    hotspot.file_path,
                    str(hotspot.churn_count),
                    f"{risk_indicator} {hotspot.risk_score:.1f}",
                    hotspot.top_authors[0] if hotspot.top_authors else "N/A"
                )

            console.print(table)
            console.print(f"\n[dim]These files have high modification frequency and increasing complexity[/dim]")
            console.print(f"[dim]Consider refactoring to reduce technical debt[/dim]\n")

        except Exception as e:
            logger.error(f"Failed to find code hotspots: {e}", exc_info=True)
            console.print(f"\n[red]❌ Error:[/red] {e}")
            raise click.Abort()


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI")
@click.option("--neo4j-user", default=None, help="Neo4j username")
@click.option("--neo4j-password", default=None, help="Neo4j password")
@click.option("--strategy", type=click.Choice(["recent", "all", "milestones"]), default="recent", help="Commit selection strategy")
@click.option("--max-commits", type=int, default=10, help="Maximum commits to analyze (default: 10)")
@click.option("--branch", default="HEAD", help="Branch to analyze (default: HEAD)")
@click.option("--generate-clues", is_flag=True, default=False, help="Generate semantic clues for each commit")
@click.pass_context
def history(ctx, repo_path: str, neo4j_uri, neo4j_user, neo4j_password, strategy: str, max_commits: int, branch: str, generate_clues: bool) -> None:
    """Ingest Git history for temporal analysis.

    Analyzes code evolution across Git commits to track:
    - Metric trends over time
    - Code quality degradation
    - Technical debt velocity

    Strategies:
      recent      - Last N commits (default, fast)
      milestones  - Tagged releases only
      all         - All commits (expensive)

    Example:
        falkor history /path/to/repo --strategy recent --max-commits 10
    """
    config = ctx.obj['config']

    console.print(f"\n[bold cyan]📊 Temporal Code Analysis[/bold cyan]\n")
    console.print(f"Repository: [yellow]{repo_path}[/yellow]")
    console.print(f"Strategy: [cyan]{strategy}[/cyan]")
    console.print(f"Max commits: [cyan]{max_commits}[/cyan]\n")

    try:
        # Get Neo4j connection details
        uri = neo4j_uri or config.neo4j.uri
        user = neo4j_user or config.neo4j.user
        password = neo4j_password or config.neo4j.password or click.prompt("Neo4j password", hide_input=True)

        # Connect to Neo4j
        client = Neo4jClient(uri=uri, username=user, password=password)

        # Create temporal ingestion pipeline
        from repotoire.pipeline.temporal_ingestion import TemporalIngestionPipeline
        pipeline = TemporalIngestionPipeline(
            repo_path=repo_path,
            neo4j_client=client,
            generate_clues=generate_clues
        )

        # Ingest with history
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"[cyan]Ingesting {strategy} commits...", total=None)

            result = pipeline.ingest_with_history(
                strategy=strategy,
                max_commits=max_commits,
                branch=branch
            )

            progress.update(task, completed=True)

        # Display results
        console.print(f"\n[green]✓ Temporal ingestion complete![/green]\n")

        results_table = Table(box=box.SIMPLE, show_header=False)
        results_table.add_column("Metric", style="bold")
        results_table.add_column("Value", style="cyan")

        results_table.add_row("Sessions created", str(result["sessions_created"]))
        results_table.add_row("Entities created", str(result["entities_created"]))
        results_table.add_row("Relationships created", str(result["relationships_created"]))
        results_table.add_row("Commits processed", str(result["commits_processed"]))

        console.print(results_table)
        console.print()

    except Exception as e:
        logger.error(f"Failed to ingest history: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@cli.command()
@click.argument("before_commit")
@click.argument("after_commit")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI")
@click.option("--neo4j-user", default=None, help="Neo4j username")
@click.option("--neo4j-password", default=None, help="Neo4j password")
@click.pass_context
def compare(ctx, before_commit: str, after_commit: str, neo4j_uri, neo4j_user, neo4j_password) -> None:
    """Compare code metrics between two commits.

    Shows how code quality metrics changed between commits:
    - Improvements (metrics got better)
    - Regressions (metrics got worse)
    - Percentage changes

    Example:
        falkor compare abc123 def456
    """
    config = ctx.obj['config']

    try:
        # Get Neo4j connection details
        uri = neo4j_uri or config.neo4j.uri
        user = neo4j_user or config.neo4j.user
        password = neo4j_password or config.neo4j.password or click.prompt("Neo4j password", hide_input=True)

        # Connect to Neo4j
        client = Neo4jClient(uri=uri, username=user, password=password)

        # Create temporal metrics analyzer
        from repotoire.detectors.temporal_metrics import TemporalMetrics
        analyzer = TemporalMetrics(client)

        with console.status(f"[bold green]Comparing commits {before_commit[:7]} → {after_commit[:7]}...", spinner="dots"):
            comparison = analyzer.compare_commits(before_commit, after_commit)

        if not comparison:
            console.print(f"\n[yellow]⚠️  Could not find sessions for commits {before_commit[:7]} and {after_commit[:7]}[/yellow]")
            console.print("[dim]Make sure you've run 'falkor history' first to ingest commit data[/dim]\n")
            return

        # Display comparison
        console.print(f"\n[bold cyan]📊 Commit Comparison[/bold cyan]\n")
        console.print(f"Before: [yellow]{comparison['before_commit']}[/yellow]  ({comparison['before_date']})")
        console.print(f"After:  [yellow]{comparison['after_commit']}[/yellow]  ({comparison['after_date']})\n")

        # Show improvements
        if comparison["improvements"]:
            console.print("[bold green]✓ Improvements:[/bold green]")
            for metric in comparison["improvements"]:
                change = comparison["changes"][metric]
                console.print(f"  • {metric}: {change['before']:.2f} → {change['after']:.2f} ({change['change_percentage']:+.1f}%)")
            console.print()

        # Show regressions
        if comparison["regressions"]:
            console.print("[bold red]⚠️  Regressions:[/bold red]")
            for metric in comparison["regressions"]:
                change = comparison["changes"][metric]
                console.print(f"  • {metric}: {change['before']:.2f} → {change['after']:.2f} ({change['change_percentage']:+.1f}%)")
            console.print()

        # Overall assessment
        if len(comparison["improvements"]) > len(comparison["regressions"]):
            console.print("[green]Overall: Code quality improved ✓[/green]")
        elif len(comparison["regressions"]) > len(comparison["improvements"]):
            console.print("[red]Overall: Code quality degraded ⚠️[/red]")
        else:
            console.print("[yellow]Overall: Mixed changes[/yellow]")

        console.print()

    except Exception as e:
        logger.error(f"Failed to compare commits: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@cli.command()
@click.option("--output-dir", "-o", type=click.Path(), default="./mcp_server", help="Output directory for generated server")
@click.option("--server-name", default="mcp_server", help="Name for the generated MCP server")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.option("--enable-rag", is_flag=True, default=False, help="Enable RAG enhancements (requires OpenAI API key)")
@click.option("--min-params", default=2, help="Minimum parameters for public functions")
@click.option("--max-params", default=10, help="Maximum parameters for public functions")
@click.option("--max-routes", default=None, type=int, help="Maximum FastAPI routes to include")
@click.option("--max-commands", default=None, type=int, help="Maximum Click commands to include")
@click.option("--max-functions", default=None, type=int, help="Maximum public functions to include")
@click.pass_context
def generate_mcp(
    ctx: click.Context,
    output_dir: str,
    server_name: str,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    enable_rag: bool,
    min_params: int,
    max_params: int,
    max_routes: int | None,
    max_commands: int | None,
    max_functions: int | None,
) -> None:
    """Generate MCP (Model Context Protocol) server from codebase.

    Automatically detects FastAPI routes, Click commands, and public functions,
    then generates a complete runnable MCP server with enhanced descriptions.

    Examples:
        # Basic generation
        repotoire generate-mcp

        # With RAG enhancements
        repotoire generate-mcp --enable-rag

        # Custom output and limits
        repotoire generate-mcp -o ./my_server --max-routes 5 --max-functions 10
    """
    from repotoire.mcp import PatternDetector, SchemaGenerator, ServerGenerator
    from repotoire.ai.embeddings import CodeEmbedder
    from repotoire.ai.retrieval import GraphRAGRetriever
    from pathlib import Path
    import os

    try:
        config = get_config()

        # Get Neo4j connection details
        uri = neo4j_uri or config.neo4j.uri
        user = neo4j_user or config.neo4j.user
        password = neo4j_password or config.neo4j.password

        if not password:
            password = click.prompt("Neo4j password", hide_input=True)

        # Get repository path (assume current directory or from config)
        repository_path = os.getcwd()

        console.print()
        console.print("[bold cyan]🚀 MCP Server Generation[/bold cyan]")
        console.print("[dim]Generating Model Context Protocol server from codebase[/dim]")
        console.print()

        # Connect to Neo4j
        with console.status("[bold green]Connecting to Neo4j...", spinner="dots"):
            client = Neo4jClient(uri=uri, username=user, password=password)

        console.print("[green]✓[/green] Connected to Neo4j")

        # Check if embeddings exist for RAG
        if enable_rag:
            stats = client.get_stats()
            embeddings_count = stats.get("embeddings_count", 0)

            if embeddings_count == 0:
                console.print("[yellow]⚠️  No embeddings found in database[/yellow]")
                console.print("[dim]Run 'repotoire ingest --generate-embeddings' first to enable RAG[/dim]")
                enable_rag = False
            else:
                console.print(f"[cyan]🔮 RAG Enhancement: Enabled ({embeddings_count:,} embeddings)[/cyan]")

        console.print()

        # Phase 1: Pattern Detection
        console.print("[bold cyan]📍 Phase 1: Pattern Detection[/bold cyan]")
        with console.status("[bold green]Detecting patterns...", spinner="dots"):
            # Enable import validation to filter out non-importable functions
            detector = PatternDetector(client, repo_path=repository_path, validate_imports=True)

            routes = detector.detect_fastapi_routes()
            commands = detector.detect_click_commands()
            functions = detector.detect_public_functions(min_params=min_params, max_params=max_params)

            # Apply limits if specified
            if max_routes is not None:
                routes = routes[:max_routes]
            if max_commands is not None:
                commands = commands[:max_commands]
            if max_functions is not None:
                functions = functions[:max_functions]

            all_patterns = routes + commands + functions

        if not all_patterns:
            console.print("[yellow]⚠️  No patterns detected in codebase[/yellow]")
            console.print("[dim]Make sure you've run 'repotoire ingest' first[/dim]")
            client.close()
            return

        console.print(f"[green]✓[/green] Detected {len(all_patterns)} patterns:")
        console.print(f"   • {len(routes)} FastAPI routes")
        console.print(f"   • {len(commands)} Click commands")
        console.print(f"   • {len(functions)} public functions")
        console.print()

        # Phase 2: Schema Generation
        console.print("[bold cyan]📋 Phase 2: Schema Generation[/bold cyan]")

        # Setup RAG if enabled
        rag_retriever = None

        if enable_rag:
            try:
                api_key = os.getenv("OPENAI_API_KEY")
                if api_key:
                    embedder = CodeEmbedder(api_key=api_key)
                    rag_retriever = GraphRAGRetriever(neo4j_client=client, embedder=embedder)
                    console.print("[cyan]🔮 RAG enhancements enabled[/cyan]")
                else:
                    console.print("[yellow]⚠️  OPENAI_API_KEY not set, RAG disabled[/yellow]")
                    enable_rag = False
            except ImportError:
                console.print("[yellow]⚠️  OpenAI package not installed, RAG disabled[/yellow]")
                enable_rag = False

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[green]Generating schemas...", total=len(all_patterns))

            # SchemaGenerator creates OpenAI client internally from env var
            generator = SchemaGenerator(
                rag_retriever=rag_retriever,
                neo4j_client=client if enable_rag else None
            )

            schemas = []
            for pattern in all_patterns:
                schema = generator.generate_tool_schema(pattern)
                schemas.append(schema)
                progress.advance(task)

        console.print(f"[green]✓[/green] Generated {len(schemas)} tool schemas")
        console.print()

        # Phase 3: Server Generation
        console.print("[bold cyan]🔧 Phase 3: Server Generation[/bold cyan]")

        output_path = Path(output_dir)
        with console.status("[bold green]Generating server code...", spinner="dots"):
            server_gen = ServerGenerator(output_path)
            server_file = server_gen.generate_server(
                patterns=all_patterns,
                schemas=schemas,
                server_name=server_name,
                repository_path=repository_path
            )

        console.print(f"[green]✓[/green] Generated MCP server")
        console.print()

        # Display results
        server_code = server_file.read_text()
        lines_of_code = len(server_code.splitlines())
        file_size_kb = len(server_code) / 1024

        # Create results panel
        panel_content = f"""[bold cyan]Server File:[/bold cyan] {server_file}
[bold cyan]Lines of Code:[/bold cyan] {lines_of_code:,}
[bold cyan]File Size:[/bold cyan] {file_size_kb:.1f} KB
[bold cyan]Tools Registered:[/bold cyan] {len(schemas)}
[bold cyan]RAG Enhanced:[/bold cyan] {'Yes' if enable_rag else 'No'}"""

        panel = Panel(
            panel_content,
            title="✅ MCP Server Generated",
            border_style="green",
            box=box.ROUNDED,
        )
        console.print(panel)
        console.print()

        # Next steps
        console.print("[bold cyan]💡 Next Steps:[/bold cyan]")
        console.print(f"   1. Test server: [dim]python {server_file}[/dim]")
        console.print(f"   2. Install MCP SDK: [dim]pip install mcp[/dim]")
        console.print(f"   3. Connect to Claude Desktop:")
        console.print()
        console.print('[dim]   Add to ~/Library/Application Support/Claude/claude_desktop_config.json:[/dim]')
        console.print('[dim]   {[/dim]')
        console.print('[dim]     "mcpServers": {[/dim]')
        console.print(f'[dim]       "{server_name}": {{[/dim]')
        console.print('[dim]         "command": "python",[/dim]')
        console.print(f'[dim]         "args": ["{server_file}"][/dim]')
        console.print('[dim]       }[/dim]')
        console.print('[dim]     }[/dim]')
        console.print('[dim]   }[/dim]')
        console.print()

        client.close()

    except Exception as e:
        logger.error(f"Failed to generate MCP server: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@cli.group()
def schema() -> None:
    """Manage and inspect graph schema.

    Tools for exploring the Neo4j graph structure, validating integrity,
    and debugging without opening Neo4j Browser.

    Examples:
        falkor schema inspect           # Show graph statistics
        falkor schema visualize         # ASCII art graph structure
        falkor schema sample Class --limit 3  # Sample Class nodes
        falkor schema validate          # Check schema integrity
    """
    pass


@schema.command()
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
@click.pass_context
def inspect(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    format: str,
) -> None:
    """Show graph statistics and schema overview."""
    try:
        config = get_config()

        # Override config with CLI args
        uri = neo4j_uri or config.neo4j_uri
        user = neo4j_user or config.neo4j_user
        password = neo4j_password or config.neo4j_password

        if not password:
            password = click.prompt("Neo4j password", hide_input=True)

        # Connect to Neo4j
        client = Neo4jClient(uri=uri, user=user, password=password)

        # Get statistics
        stats = client.get_stats()
        node_counts = client.get_node_label_counts()
        rel_counts = client.get_relationship_type_counts()

        if format == "json":
            import json
            output = {
                "total_nodes": stats.get("total_nodes", 0),
                "total_relationships": stats.get("total_relationships", 0),
                "node_types": node_counts,
                "relationship_types": rel_counts,
            }
            console.print(json.dumps(output, indent=2))
        else:
            # Create panel with overview
            panel_content = f"[bold]Total Nodes:[/bold] {stats.get('total_nodes', 0):,}\n"
            panel_content += f"[bold]Total Relationships:[/bold] {stats.get('total_relationships', 0):,}\n"

            panel = Panel(
                panel_content,
                title="Graph Schema Overview",
                border_style="cyan",
                box=box.ROUNDED,
            )
            console.print(panel)
            console.print()

            # Node types table
            node_table = Table(title="Node Types", box=box.ROUNDED, show_header=True, header_style="bold cyan")
            node_table.add_column("Type", style="cyan")
            node_table.add_column("Count", justify="right", style="green")

            for label, count in node_counts.items():
                node_table.add_row(label, f"{count:,}")

            console.print(node_table)
            console.print()

            # Relationship types table
            rel_table = Table(title="Relationship Types", box=box.ROUNDED, show_header=True, header_style="bold magenta")
            rel_table.add_column("Type", style="magenta")
            rel_table.add_column("Count", justify="right", style="green")

            for rel_type, count in rel_counts.items():
                rel_table.add_row(rel_type, f"{count:,}")

            console.print(rel_table)

        client.close()

    except Exception as e:
        logger.error(f"Failed to inspect schema: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@schema.command()
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def visualize(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
) -> None:
    """Visualize graph schema structure with ASCII art."""
    try:
        config = get_config()

        # Override config with CLI args
        uri = neo4j_uri or config.neo4j_uri
        user = neo4j_user or config.neo4j_user
        password = neo4j_password or config.neo4j_password

        if not password:
            password = click.prompt("Neo4j password", hide_input=True)

        # Connect to Neo4j
        client = Neo4jClient(uri=uri, user=user, password=password)

        # Get relationship type counts to understand schema
        rel_counts = client.get_relationship_type_counts()

        # Create ASCII art visualization
        console.print()
        console.print("[bold cyan]Graph Schema Structure[/bold cyan]")
        console.print()

        # Build schema tree
        tree = Tree("🗂️  [bold cyan](File)[/bold cyan]", guide_style="cyan")

        if "CONTAINS" in rel_counts:
            contains_branch = tree.add("│")
            contains_branch.add("├─[[bold magenta]CONTAINS[/bold magenta]]─> [bold yellow](Class)[/bold yellow]")
            class_branch = contains_branch.add("│")

            if "INHERITS" in rel_counts:
                class_branch.add("  ├─[[bold magenta]INHERITS[/bold magenta]]─> [bold yellow](Class)[/bold yellow]")

            class_branch.add("  └─[[bold magenta]DEFINES[/bold magenta]]─> [bold green](Function)[/bold green]")

            func_branch = tree.add("│")
            func_branch.add("├─[[bold magenta]CONTAINS[/bold magenta]]─> [bold green](Function)[/bold green]")

            if "CALLS" in rel_counts:
                func_sub = func_branch.add("│")
                func_sub.add("  └─[[bold magenta]CALLS[/bold magenta]]─> [bold green](Function)[/bold green]")

        if "IMPORTS" in rel_counts:
            tree.add("│")
            tree.add("└─[[bold magenta]IMPORTS[/bold magenta]]───> [bold cyan](File)[/bold cyan]")

        console.print(tree)
        console.print()

        # Print relationship stats
        console.print("[bold]Relationship Counts:[/bold]")
        for rel_type, count in rel_counts.items():
            console.print(f"  • {rel_type}: {count:,}")

        console.print()
        client.close()

    except Exception as e:
        logger.error(f"Failed to visualize schema: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@schema.command()
@click.argument("node_type")
@click.option("--limit", default=3, help="Number of samples to show")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def sample(
    ctx: click.Context,
    node_type: str,
    limit: int,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
) -> None:
    """Show sample nodes of a specific type.

    NODE_TYPE: The node label to sample (e.g., Class, Function, File)
    """
    try:
        config = get_config()

        # Override config with CLI args
        uri = neo4j_uri or config.neo4j_uri
        user = neo4j_user or config.neo4j_user
        password = neo4j_password or config.neo4j_password

        if not password:
            password = click.prompt("Neo4j password", hide_input=True)

        # Connect to Neo4j
        client = Neo4jClient(uri=uri, user=user, password=password)

        # Get total count
        node_counts = client.get_node_label_counts()
        total_count = node_counts.get(node_type, 0)

        if total_count == 0:
            console.print(f"[yellow]No nodes of type '{node_type}' found[/yellow]")
            client.close()
            return

        # Get samples
        samples = client.sample_nodes(node_type, limit)

        # Display samples
        console.print()
        panel_title = f"Sample {node_type} Nodes ({min(limit, len(samples))} of {total_count:,})"

        sample_text = ""
        for i, props in enumerate(samples, 1):
            sample_text += f"\n[bold cyan]{i}. {props.get('qualifiedName', props.get('filePath', 'Unknown'))}[/bold cyan]\n"

            # Show key properties
            for key, value in sorted(props.items()):
                if key not in ['qualifiedName', 'filePath'] and value is not None:
                    # Truncate long values
                    str_val = str(value)
                    if len(str_val) > 100:
                        str_val = str_val[:97] + "..."
                    sample_text += f"   [dim]• {key}:[/dim] {str_val}\n"

            if i < len(samples):
                sample_text += "\n"

        panel = Panel(
            sample_text.strip(),
            title=panel_title,
            border_style="cyan",
            box=box.ROUNDED,
        )
        console.print(panel)
        console.print()

        client.close()

    except Exception as e:
        logger.error(f"Failed to sample nodes: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@schema.command()
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-user", default=None, help="Neo4j username (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def validate(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
) -> None:
    """Validate graph schema integrity."""
    try:
        config = get_config()

        # Override config with CLI args
        uri = neo4j_uri or config.neo4j_uri
        user = neo4j_user or config.neo4j_user
        password = neo4j_password or config.neo4j_password

        if not password:
            password = click.prompt("Neo4j password", hide_input=True)

        # Connect to Neo4j
        client = Neo4jClient(uri=uri, user=user, password=password)

        console.print()
        console.print("[bold cyan]Validating Graph Schema...[/bold cyan]")
        console.print()

        # Run validation
        validation = client.validate_schema_integrity()

        if validation["valid"]:
            console.print("[green]✓ Schema validation passed[/green]")
            console.print("[green]✓ All integrity checks passed[/green]")
        else:
            console.print("[red]✗ Schema validation failed[/red]")
            console.print()
            console.print("[bold]Issues Found:[/bold]")

            for issue_type, count in validation["issues"].items():
                issue_name = issue_type.replace("_", " ").title()
                console.print(f"  [red]✗[/red] {issue_name}: {count:,}")

            console.print()
            console.print("[yellow]Run 'falkor schema inspect' for more details[/yellow]")

        console.print()
        client.close()

    except Exception as e:
        logger.error(f"Failed to validate schema: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


# ============================================================================
# Rule Management Commands (REPO-125)
# ============================================================================

@cli.group()
def rule() -> None:
    """Manage custom code quality rules (REPO-125).

    Rules are stored as graph nodes with time-based priority refresh.
    Frequently-used rules automatically bubble to the top for RAG context.

    Examples:
        repotoire rule list                    # List all rules
        repotoire rule add rules.yaml          # Add rules from file
        repotoire rule test no-god-classes     # Dry-run a rule
        repotoire rule stats                   # Show rule statistics
    """
    pass


@rule.command()
@click.option("--enabled-only", is_flag=True, help="Only show enabled rules")
@click.option("--tags", multiple=True, help="Filter by tags")
@click.option("--sort-by", type=click.Choice(["priority", "name", "last-used"]), default="priority", help="Sort order")
@click.option("--limit", type=int, help="Maximum rules to show")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def list(
    ctx: click.Context,
    enabled_only: bool,
    tags: tuple,
    sort_by: str,
    limit: int | None,
    neo4j_uri: str | None,
    neo4j_password: str | None,
) -> None:
    """List all custom rules with priority scores."""
    try:
        from repotoire.rules.engine import RuleEngine

        # Get Neo4j config
        config = ctx.obj or get_config()
        uri = neo4j_uri or config.neo4j_uri
        password = neo4j_password or config.neo4j_password

        # Connect
        client = Neo4jClient(uri=uri, password=password)
        engine = RuleEngine(client)

        # Get rules
        rules = engine.list_rules(
            enabled_only=enabled_only,
            tags=list(tags) if tags else None,
            limit=limit
        )

        if not rules:
            console.print("\n[yellow]No rules found.[/yellow]")
            console.print("💡 Add rules with: [cyan]repotoire rule add rules.yaml[/cyan]\n")
            return

        # Calculate priorities and sort
        rules_with_priority = [(rule, rule.calculate_priority()) for rule in rules]

        if sort_by == "priority":
            rules_with_priority.sort(key=lambda x: x[1], reverse=True)
        elif sort_by == "name":
            rules_with_priority.sort(key=lambda x: x[0].name)
        elif sort_by == "last-used":
            rules_with_priority.sort(key=lambda x: x[0].lastUsed or "", reverse=True)

        # Display table
        table = Table(title=f"Custom Rules ({len(rules)} found)", box=box.ROUNDED)
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("Severity", style="yellow")
        table.add_column("Priority", justify="right")
        table.add_column("Accessed", justify="right")
        table.add_column("Last Used", style="dim")
        table.add_column("Enabled", justify="center")

        for rule, priority in rules_with_priority:
            # Format last used
            last_used_str = "Never"
            if rule.lastUsed:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                # Handle timezone-naive lastUsed
                last_used = rule.lastUsed
                if last_used.tzinfo is None:
                    last_used = last_used.replace(tzinfo=timezone.utc)
                delta = now - last_used

                # Simple human-readable format
                if delta.days > 365:
                    last_used_str = f"{delta.days // 365}y ago"
                elif delta.days > 30:
                    last_used_str = f"{delta.days // 30}mo ago"
                elif delta.days > 0:
                    last_used_str = f"{delta.days}d ago"
                elif delta.seconds > 3600:
                    last_used_str = f"{delta.seconds // 3600}h ago"
                elif delta.seconds > 60:
                    last_used_str = f"{delta.seconds // 60}m ago"
                else:
                    last_used_str = "Just now"

            # Enabled indicator
            enabled_icon = "✓" if rule.enabled else "✗"
            enabled_style = "green" if rule.enabled else "red"

            table.add_row(
                rule.id,
                rule.name,
                rule.severity.value.upper(),
                f"{priority:.1f}",
                str(rule.accessCount),
                last_used_str,
                f"[{enabled_style}]{enabled_icon}[/{enabled_style}]"
            )

        console.print()
        console.print(table)
        console.print()

        client.close()

    except Exception as e:
        logger.error(f"Failed to list rules: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@rule.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def add(
    ctx: click.Context,
    file_path: str,
    neo4j_uri: str | None,
    neo4j_password: str | None,
) -> None:
    """Add rules from a YAML file.

    The YAML file should contain a list of rules with the following structure:

    \b
    rules:
      - id: no-god-classes
        name: "Classes should have fewer than 20 methods"
        description: "Large classes violate SRP"
        pattern: |
          MATCH (c:Class)-[:CONTAINS]->(m:Function)
          WITH c, count(m) as method_count
          WHERE method_count > 20
          RETURN c.qualifiedName as class_name, method_count
        severity: HIGH
        userPriority: 100
        tags: [complexity, architecture]
        autoFix: "Split into smaller classes"
    """
    try:
        import yaml
        from repotoire.rules.engine import RuleEngine
        from repotoire.rules.validator import RuleValidator
        from repotoire.models import Rule, Severity

        # Get Neo4j config
        config = ctx.obj or get_config()
        uri = neo4j_uri or config.neo4j_uri
        password = neo4j_password or config.neo4j_password

        # Connect
        client = Neo4jClient(uri=uri, password=password)
        engine = RuleEngine(client)
        validator = RuleValidator(client)

        # Load YAML
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)

        rules_data = data.get('rules', [])
        if not rules_data:
            console.print(f"\n[yellow]No rules found in {file_path}[/yellow]")
            return

        console.print(f"\n[bold]Adding {len(rules_data)} rules from {file_path}...[/bold]\n")

        success_count = 0
        error_count = 0

        for rule_data in rules_data:
            rule_id = rule_data.get('id')
            try:
                # Validate pattern
                pattern = rule_data.get('pattern')
                is_valid, error = validator.validate_pattern(pattern)
                if not is_valid:
                    console.print(f"  [red]✗[/red] {rule_id}: Invalid pattern - {error}")
                    error_count += 1
                    continue

                # Create Rule object
                rule = Rule(
                    id=rule_id,
                    name=rule_data['name'],
                    description=rule_data['description'],
                    pattern=pattern,
                    severity=Severity(rule_data.get('severity', 'medium').lower()),
                    enabled=rule_data.get('enabled', True),
                    userPriority=rule_data.get('userPriority', 50),
                    autoFix=rule_data.get('autoFix'),
                    tags=rule_data.get('tags', []),
                )

                # Create in database
                engine.create_rule(rule)
                console.print(f"  [green]✓[/green] {rule_id}: Added successfully")
                success_count += 1

            except ValueError as e:
                if "already exists" in str(e):
                    console.print(f"  [yellow]⚠[/yellow] {rule_id}: Already exists (skipping)")
                else:
                    console.print(f"  [red]✗[/red] {rule_id}: {e}")
                    error_count += 1
            except Exception as e:
                console.print(f"  [red]✗[/red] {rule_id}: {e}")
                error_count += 1

        # Summary
        console.print(f"\n[bold]Summary:[/bold]")
        console.print(f"  [green]✓ Added:[/green] {success_count}")
        console.print(f"  [red]✗ Failed:[/red] {error_count}")
        console.print()

        client.close()

    except Exception as e:
        logger.error(f"Failed to add rules: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@rule.command()
@click.argument("rule_id")
@click.option("--name", help="Update rule name")
@click.option("--priority", type=int, help="Update user priority (0-1000)")
@click.option("--enable/--disable", default=None, help="Enable or disable rule")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def edit(
    ctx: click.Context,
    rule_id: str,
    name: str | None,
    priority: int | None,
    enable: bool | None,
    neo4j_uri: str | None,
    neo4j_password: str | None,
) -> None:
    """Edit an existing rule."""
    try:
        from repotoire.rules.engine import RuleEngine

        # Get Neo4j config
        config = ctx.obj or get_config()
        uri = neo4j_uri or config.neo4j_uri
        password = neo4j_password or config.neo4j_password

        # Connect
        client = Neo4jClient(uri=uri, password=password)
        engine = RuleEngine(client)

        # Check rule exists
        rule = engine.get_rule(rule_id)
        if not rule:
            console.print(f"\n[red]❌ Rule '{rule_id}' not found[/red]\n")
            return

        # Build updates
        updates = {}
        if name:
            updates['name'] = name
        if priority is not None:
            updates['userPriority'] = priority
        if enable is not None:
            updates['enabled'] = enable

        if not updates:
            console.print("\n[yellow]No updates specified. Use --name, --priority, or --enable/--disable[/yellow]\n")
            return

        # Update
        updated_rule = engine.update_rule(rule_id, **updates)

        console.print(f"\n[green]✓ Updated rule '{rule_id}'[/green]")
        console.print(f"  Priority: {updated_rule.calculate_priority():.1f}")
        console.print(f"  Enabled: {updated_rule.enabled}\n")

        client.close()

    except Exception as e:
        logger.error(f"Failed to edit rule: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@rule.command()
@click.argument("rule_id")
@click.confirmation_option(prompt="Are you sure you want to delete this rule?")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def delete(
    ctx: click.Context,
    rule_id: str,
    neo4j_uri: str | None,
    neo4j_password: str | None,
) -> None:
    """Delete a rule."""
    try:
        from repotoire.rules.engine import RuleEngine

        # Get Neo4j config
        config = ctx.obj or get_config()
        uri = neo4j_uri or config.neo4j_uri
        password = neo4j_password or config.neo4j_password

        # Connect
        client = Neo4jClient(uri=uri, password=password)
        engine = RuleEngine(client)

        # Delete
        deleted = engine.delete_rule(rule_id)

        if deleted:
            console.print(f"\n[green]✓ Deleted rule '{rule_id}'[/green]\n")
        else:
            console.print(f"\n[yellow]Rule '{rule_id}' not found[/yellow]\n")

        client.close()

    except Exception as e:
        logger.error(f"Failed to delete rule: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@rule.command()
@click.argument("rule_id")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def test(
    ctx: click.Context,
    rule_id: str,
    neo4j_uri: str | None,
    neo4j_password: str | None,
) -> None:
    """Test a rule (dry-run) to see what it would find."""
    try:
        from repotoire.rules.engine import RuleEngine

        # Get Neo4j config
        config = ctx.obj or get_config()
        uri = neo4j_uri or config.neo4j_uri
        password = neo4j_password or config.neo4j_password

        # Connect
        client = Neo4jClient(uri=uri, password=password)
        engine = RuleEngine(client)

        # Get rule
        rule = engine.get_rule(rule_id)
        if not rule:
            console.print(f"\n[red]❌ Rule '{rule_id}' not found[/red]\n")
            return

        console.print(f"\n[bold cyan]Testing rule: {rule.name}[/bold cyan]")
        console.print(f"Pattern:\n{rule.pattern}\n")

        with console.status(f"[bold green]Executing rule..."):
            findings = engine.execute_rule(rule)

        console.print(f"\n[bold]Found {len(findings)} violations:[/bold]\n")

        if findings:
            for i, finding in enumerate(findings[:10], 1):  # Show first 10
                console.print(f"{i}. [{finding.severity.value}] {finding.title}")
                console.print(f"   {finding.description}")
                if finding.affected_files:
                    console.print(f"   Files: {', '.join(finding.affected_files)}")
                console.print()

            if len(findings) > 10:
                console.print(f"... and {len(findings) - 10} more\n")
        else:
            console.print("[green]No violations found ✓[/green]\n")

        client.close()

    except Exception as e:
        logger.error(f"Failed to test rule: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@rule.command()
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def stats(
    ctx: click.Context,
    neo4j_uri: str | None,
    neo4j_password: str | None,
) -> None:
    """Show rule usage statistics."""
    try:
        from repotoire.rules.engine import RuleEngine

        # Get Neo4j config
        config = ctx.obj or get_config()
        uri = neo4j_uri or config.neo4j_uri
        password = neo4j_password or config.neo4j_password

        # Connect
        client = Neo4jClient(uri=uri, password=password)
        engine = RuleEngine(client)

        # Get statistics
        stats_data = engine.get_rule_statistics()

        # Display panel
        panel_content = f"""
[cyan]Total Rules:[/cyan] {stats_data.get('total_rules', 0)}
[green]Enabled Rules:[/green] {stats_data.get('enabled_rules', 0)}
[yellow]Average Access Count:[/yellow] {stats_data.get('avg_access_count', 0):.1f}
[bold]Total Executions:[/bold] {stats_data.get('total_executions', 0)}
[magenta]Max Access Count:[/magenta] {stats_data.get('max_access_count', 0)}
        """

        console.print()
        console.print(Panel(panel_content.strip(), title="Rule Statistics", border_style="cyan"))
        console.print()

        # Show hottest rules
        hot_rules = engine.get_hot_rules(top_k=5)
        if hot_rules:
            console.print("[bold]🔥 Hottest Rules (Top 5):[/bold]\n")
            for i, rule in enumerate(hot_rules, 1):
                priority = rule.calculate_priority()
                console.print(f"{i}. {rule.id} (priority: {priority:.1f}, accessed: {rule.accessCount} times)")
            console.print()

        client.close()

    except Exception as e:
        logger.error(f"Failed to get stats: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@rule.command("daemon-refresh")
@click.option("--decay-threshold", default=7, help="Days before decaying stale rules (default: 7)")
@click.option("--decay-factor", default=0.9, help="Priority decay multiplier (default: 0.9)")
@click.option("--auto-archive", is_flag=True, help="Archive rules unused for >90 days")
@click.option("--neo4j-uri", default=None, help="Neo4j connection URI (overrides config)")
@click.option("--neo4j-password", default=None, help="Neo4j password (overrides config)")
@click.pass_context
def daemon_refresh(
    ctx: click.Context,
    decay_threshold: int,
    decay_factor: float,
    auto_archive: bool,
    neo4j_uri: str | None,
    neo4j_password: str | None,
) -> None:
    """Force immediate priority refresh for all rules.

    This command runs the daemon's refresh cycle once:
    - Decays stale rules (not used in >N days)
    - Optionally archives very old rules (>90 days)
    - Shows statistics

    Examples:
        # Standard refresh (decay after 7 days)
        repotoire rule daemon-refresh

        # Aggressive decay (after 3 days, reduce by 20%)
        repotoire rule daemon-refresh --decay-threshold 3 --decay-factor 0.8

        # Archive very old rules
        repotoire rule daemon-refresh --auto-archive
    """
    try:
        from repotoire.rules.daemon import RuleRefreshDaemon

        # Get Neo4j config
        config = ctx.obj or get_config()
        uri = neo4j_uri or config.neo4j_uri
        password = neo4j_password or config.neo4j_password

        # Connect
        client = Neo4jClient(uri=uri, password=password)

        # Create daemon
        daemon = RuleRefreshDaemon(
            client,
            decay_threshold_days=decay_threshold,
            decay_factor=decay_factor,
            auto_archive=auto_archive,
        )

        console.print("\n[cyan]🔄 Running priority refresh...[/cyan]\n")

        # Force refresh
        results = daemon.force_refresh()

        # Display results
        panel_content = f"""
[yellow]Decayed Rules:[/yellow] {results['decayed']} rules reduced in priority
[red]Archived Rules:[/red] {results['archived']} rules disabled (very old)

[bold]Current Statistics:[/bold]
  [green]Active Rules:[/green] {results['stats'].get('active_rules', 0):.0f}
  [dim]Archived Rules:[/dim] {results['stats'].get('archived_rules', 0):.0f}
  [yellow]Stale Rules:[/yellow] {results['stats'].get('stale_rules', 0):.0f} (>{decay_threshold}d since use)
  [cyan]Average Age:[/cyan] {results['stats'].get('avg_days_since_use', 0):.1f} days
        """

        console.print(Panel(panel_content.strip(), title="Refresh Results", border_style="green"))
        console.print()

        if results['decayed'] > 0:
            console.print(f"[green]✓[/green] Reduced priority of {results['decayed']} stale rules")
        else:
            console.print("[dim]No stale rules to decay[/dim]")

        if auto_archive and results['archived'] > 0:
            console.print(f"[yellow]✓[/yellow] Archived {results['archived']} very old rules")

        console.print()

        client.close()

    except Exception as e:
        logger.error(f"Failed to refresh rules: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@cli.group()
def metrics() -> None:
    """Query and export historical metrics from TimescaleDB.

    Commands for analyzing code health trends, detecting regressions,
    and exporting metrics data for visualization in tools like Grafana.

    Requires TimescaleDB to be configured via REPOTOIRE_TIMESCALE_URI.

    Examples:
        repotoire metrics trend myrepo --days 30
        repotoire metrics regression myrepo
        repotoire metrics compare myrepo --start 2024-01-01 --end 2024-01-31
        repotoire metrics export myrepo --format csv --output metrics.csv
    """
    pass


@metrics.command()
@click.argument("repository")
@click.option("--branch", "-b", default="main", help="Git branch to query")
@click.option("--days", "-d", type=int, default=30, help="Number of days to look back")
@click.option("--format", "-f", type=click.Choice(["table", "json", "csv"]), default="table", help="Output format")
@click.pass_context
def trend(
    ctx: click.Context,
    repository: str,
    branch: str,
    days: int,
    format: str,
) -> None:
    """Show health score trend over time.

    Displays how code health metrics have changed over the specified time period.
    Useful for identifying gradual quality degradation or improvements.

    Example:
        repotoire metrics trend /path/to/repo --days 90 --format table
    """
    try:
        # Get config
        config: FalkorConfig = ctx.obj.get('config') or get_config()

        # Check if TimescaleDB is configured
        if not config.timescale.connection_string:
            console.print("\n[red]❌ TimescaleDB not configured[/red]")
            console.print("[dim]Set REPOTOIRE_TIMESCALE_URI environment variable[/dim]")
            raise click.Abort()

        # Import TimescaleDB client
        try:
            from repotoire.historical import TimescaleClient
        except ImportError:
            console.print("\n[red]❌ TimescaleDB support not installed[/red]")
            console.print("[dim]Install with: pip install repotoire[timescale][/dim]")
            raise click.Abort()

        # Query trend data
        with TimescaleClient(config.timescale.connection_string) as client:
            data = client.get_trend(repository, branch=branch, days=days)

        if not data:
            console.print(f"\n[yellow]No metrics found for {repository}:{branch} in the last {days} days[/yellow]")
            return

        # Display based on format
        if format == "json":
            import json
            from datetime import datetime
            # Convert datetime to string for JSON serialization
            for row in data:
                if 'time' in row and isinstance(row['time'], datetime):
                    row['time'] = row['time'].isoformat()
            console.print(json.dumps(data, indent=2))

        elif format == "csv":
            import csv
            import sys
            from io import StringIO

            output = StringIO()
            if data:
                writer = csv.DictWriter(output, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
                console.print(output.getvalue())

        else:  # table format
            table = Table(title=f"Health Trend: {repository} ({branch})")
            table.add_column("Time", style="cyan")
            table.add_column("Overall", style="bold")
            table.add_column("Structure", style="green")
            table.add_column("Quality", style="yellow")
            table.add_column("Architecture", style="blue")
            table.add_column("Issues", style="red")
            table.add_column("Critical", style="bright_red")
            table.add_column("Commit", style="dim")

            for row in data:
                table.add_row(
                    str(row['time']),
                    f"{row['overall_health']:.1f}" if row['overall_health'] else "N/A",
                    f"{row['structure_health']:.1f}" if row['structure_health'] else "N/A",
                    f"{row['quality_health']:.1f}" if row['quality_health'] else "N/A",
                    f"{row['architecture_health']:.1f}" if row['architecture_health'] else "N/A",
                    str(row['total_findings']) if row['total_findings'] is not None else "0",
                    str(row['critical_count']) if row['critical_count'] is not None else "0",
                    (row['commit_sha'][:8] if row['commit_sha'] else "N/A"),
                )

            console.print()
            console.print(table)
            console.print()

    except Exception as e:
        logger.error(f"Failed to query trend: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@metrics.command()
@click.argument("repository")
@click.option("--branch", "-b", default="main", help="Git branch to query")
@click.option("--threshold", "-t", type=float, default=5.0, help="Minimum health score drop to flag")
@click.pass_context
def regression(
    ctx: click.Context,
    repository: str,
    branch: str,
    threshold: float,
) -> None:
    """Detect if health score dropped significantly.

    Compares the most recent analysis with the previous one to identify
    sudden quality regressions that may require immediate attention.

    Example:
        repotoire metrics regression /path/to/repo --threshold 10.0
    """
    try:
        # Get config
        config: FalkorConfig = ctx.obj.get('config') or get_config()

        # Check if TimescaleDB is configured
        if not config.timescale.connection_string:
            console.print("\n[red]❌ TimescaleDB not configured[/red]")
            console.print("[dim]Set REPOTOIRE_TIMESCALE_URI environment variable[/dim]")
            raise click.Abort()

        # Import TimescaleDB client
        try:
            from repotoire.historical import TimescaleClient
        except ImportError:
            console.print("\n[red]❌ TimescaleDB support not installed[/red]")
            console.print("[dim]Install with: pip install repotoire[timescale][/dim]")
            raise click.Abort()

        # Check for regression
        with TimescaleClient(config.timescale.connection_string) as client:
            result = client.detect_regression(repository, branch=branch, threshold=threshold)

        if not result:
            console.print(f"\n[green]✓ No significant regression detected[/green]")
            console.print(f"[dim]Threshold: {threshold} points[/dim]")
            return

        # Display regression details
        console.print()
        console.print(Panel(
            f"""[bold red]⚠️  Quality Regression Detected[/bold red]

[bold]Health Score Drop:[/bold] {result['health_drop']:.1f} points

[red]Previous:[/red] {result['previous_score']:.1f} at {result['previous_time']}
  Commit: {result['previous_commit'][:8] if result['previous_commit'] else 'N/A'}

[yellow]Current:[/yellow] {result['current_score']:.1f} at {result['current_time']}
  Commit: {result['current_commit'][:8] if result['current_commit'] else 'N/A'}

[dim]This exceeds the threshold of {threshold} points.[/dim]
            """.strip(),
            title=f"Regression: {repository} ({branch})",
            border_style="red"
        ))
        console.print()

    except Exception as e:
        logger.error(f"Failed to detect regression: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@metrics.command()
@click.argument("repository")
@click.option("--branch", "-b", default="main", help="Git branch to query")
@click.option("--start", "-s", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--end", "-e", required=True, help="End date (YYYY-MM-DD)")
@click.pass_context
def compare(
    ctx: click.Context,
    repository: str,
    branch: str,
    start: str,
    end: str,
) -> None:
    """Compare metrics between two time periods.

    Calculates aggregate statistics (average, min, max) for a date range,
    useful for comparing sprint performance or release quality.

    Example:
        repotoire metrics compare /path/to/repo --start 2024-01-01 --end 2024-01-31
    """
    try:
        # Parse dates
        from datetime import datetime

        try:
            start_date = datetime.fromisoformat(start)
            end_date = datetime.fromisoformat(end)
        except ValueError as e:
            console.print(f"\n[red]❌ Invalid date format:[/red] {e}")
            console.print("[dim]Use YYYY-MM-DD format[/dim]")
            raise click.Abort()

        # Get config
        config: FalkorConfig = ctx.obj.get('config') or get_config()

        # Check if TimescaleDB is configured
        if not config.timescale.connection_string:
            console.print("\n[red]❌ TimescaleDB not configured[/red]")
            console.print("[dim]Set REPOTOIRE_TIMESCALE_URI environment variable[/dim]")
            raise click.Abort()

        # Import TimescaleDB client
        try:
            from repotoire.historical import TimescaleClient
        except ImportError:
            console.print("\n[red]❌ TimescaleDB support not installed[/red]")
            console.print("[dim]Install with: pip install repotoire[timescale][/dim]")
            raise click.Abort()

        # Query comparison data
        with TimescaleClient(config.timescale.connection_string) as client:
            stats = client.compare_periods(repository, start_date, end_date, branch=branch)

        if not stats or stats.get('num_analyses', 0) == 0:
            console.print(f"\n[yellow]No metrics found for {repository}:{branch} between {start} and {end}[/yellow]")
            return

        # Display comparison
        console.print()
        console.print(Panel(
            f"""[bold]Period:[/bold] {start} to {end}
[bold]Analyses:[/bold] {stats['num_analyses']}

[bold cyan]Health Scores:[/bold cyan]
  Average: {stats['avg_health']:.1f}
  Best:    {stats['max_health']:.1f}
  Worst:   {stats['min_health']:.1f}

[bold red]Issues:[/bold red]
  Avg per analysis: {stats['avg_issues']:.1f}
  Total critical:   {stats['total_critical']}
  Total high:       {stats['total_high']}
            """.strip(),
            title=f"Period Comparison: {repository} ({branch})",
            border_style="cyan"
        ))
        console.print()

    except Exception as e:
        logger.error(f"Failed to compare periods: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@metrics.command()
@click.argument("repository")
@click.option("--branch", "-b", default="main", help="Git branch to query")
@click.option("--days", "-d", type=int, help="Number of days to look back (optional)")
@click.option("--format", "-f", type=click.Choice(["json", "csv"]), default="json", help="Output format")
@click.option("--output", "-o", type=click.Path(), help="Output file (prints to stdout if not specified)")
@click.pass_context
def export(
    ctx: click.Context,
    repository: str,
    branch: str,
    days: int | None,
    format: str,
    output: str | None,
) -> None:
    """Export metrics data for external analysis.

    Exports raw metrics data in JSON or CSV format for use in visualization
    tools like Grafana, spreadsheets, or custom analytics pipelines.

    Example:
        repotoire metrics export /path/to/repo --format csv --output metrics.csv
    """
    try:
        # Get config
        config: FalkorConfig = ctx.obj.get('config') or get_config()

        # Check if TimescaleDB is configured
        if not config.timescale.connection_string:
            console.print("\n[red]❌ TimescaleDB not configured[/red]")
            console.print("[dim]Set REPOTOIRE_TIMESCALE_URI environment variable[/dim]")
            raise click.Abort()

        # Import TimescaleDB client
        try:
            from repotoire.historical import TimescaleClient
        except ImportError:
            console.print("\n[red]❌ TimescaleDB support not installed[/red]")
            console.print("[dim]Install with: pip install repotoire[timescale][/dim]")
            raise click.Abort()

        # Query data
        with TimescaleClient(config.timescale.connection_string) as client:
            if days:
                data = client.get_trend(repository, branch=branch, days=days)
            else:
                # Get all data (use a large number)
                data = client.get_trend(repository, branch=branch, days=365 * 10)

        if not data:
            console.print(f"\n[yellow]No metrics found for {repository}:{branch}[/yellow]")
            return

        # Export data
        if format == "csv":
            import csv
            from pathlib import Path

            if output:
                with open(output, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=data[0].keys())
                    writer.writeheader()
                    writer.writerows(data)
                console.print(f"\n[green]✓[/green] Exported {len(data)} records to {output}")
            else:
                import sys
                writer = csv.DictWriter(sys.stdout, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)

        else:  # json format
            import json
            from datetime import datetime

            # Convert datetime to string for JSON serialization
            for row in data:
                if 'time' in row and isinstance(row['time'], datetime):
                    row['time'] = row['time'].isoformat()

            json_data = json.dumps(data, indent=2)

            if output:
                with open(output, 'w') as f:
                    f.write(json_data)
                console.print(f"\n[green]✓[/green] Exported {len(data)} records to {output}")
            else:
                console.print(json_data)

    except Exception as e:
        logger.error(f"Failed to export metrics: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@cli.group()
def historical() -> None:
    """Query and analyze git history using temporal knowledge graphs.

    Commands for integrating git commit history with Graphiti temporal knowledge
    graph, enabling natural language queries about code evolution.

    Requires Graphiti to be configured via OPENAI_API_KEY and Neo4j connection.

    Examples:
        repotoire historical ingest-git /path/to/repo --since 2024-01-01
        repotoire historical query "When did we add authentication?"
        repotoire historical timeline authenticate_user --entity-type function
    """
    pass


@historical.command("ingest-git")
@click.argument("repository", type=click.Path(exists=True))
@click.option("--since", "-s", help="Only ingest commits after this date (YYYY-MM-DD)")
@click.option("--until", "-u", help="Only ingest commits before this date (YYYY-MM-DD)")
@click.option("--branch", "-b", default="main", help="Git branch to analyze")
@click.option("--max-commits", "-m", type=int, default=1000, help="Maximum commits to process")
@click.option("--batch-size", type=int, default=10, help="Commits to process in parallel")
@click.option("--neo4j-uri", envvar="REPOTOIRE_NEO4J_URI", default="bolt://localhost:7687", help="Neo4j connection URI")
@click.option("--neo4j-password", envvar="REPOTOIRE_NEO4J_PASSWORD", help="Neo4j password")
@click.pass_context
def ingest_git(
    ctx: click.Context,
    repository: str,
    since: Optional[str],
    until: Optional[str],
    branch: str,
    max_commits: int,
    batch_size: int,
    neo4j_uri: str,
    neo4j_password: Optional[str],
) -> None:
    """Ingest git commit history into Graphiti temporal knowledge graph.

    Analyzes git repository and creates Graphiti episodes for each commit,
    enabling natural language queries about code evolution over time.

    Example:
        repotoire historical ingest-git /path/to/repo --since 2024-01-01 --max-commits 500
    """
    import asyncio
    from datetime import datetime, timezone

    try:
        # Check for required dependencies
        try:
            from graphiti_core import Graphiti
            from repotoire.historical import GitGraphitiIntegration
        except ImportError as e:
            console.print("\n[red]❌ Graphiti not installed[/red]")
            console.print(
                "[dim]Install with: uv pip install 'repotoire[graphiti]' or pip install graphiti-core[/dim]"
            )
            raise click.Abort()

        # Check for OpenAI API key
        import os
        if not os.getenv("OPENAI_API_KEY"):
            console.print("\n[red]❌ OPENAI_API_KEY not set[/red]")
            console.print("[dim]Graphiti requires an OpenAI API key for LLM processing[/dim]")
            raise click.Abort()

        # Check for Neo4j password
        if not neo4j_password:
            console.print("\n[red]❌ Neo4j password not provided[/red]")
            console.print("[dim]Set REPOTOIRE_NEO4J_PASSWORD or use --neo4j-password[/dim]")
            raise click.Abort()

        # Parse dates if provided
        since_dt = None
        until_dt = None

        if since:
            try:
                since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                console.print(f"\n[red]❌ Invalid date format for --since: {since}[/red]")
                console.print("[dim]Use format: YYYY-MM-DD[/dim]")
                raise click.Abort()

        if until:
            try:
                until_dt = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                console.print(f"\n[red]❌ Invalid date format for --until: {until}[/red]")
                console.print("[dim]Use format: YYYY-MM-DD[/dim]")
                raise click.Abort()

        console.print("\n[bold]🔄 Ingesting Git History[/bold]")
        console.print(f"Repository: {repository}")
        console.print(f"Branch: {branch}")
        if since_dt:
            console.print(f"Since: {since_dt.date()}")
        if until_dt:
            console.print(f"Until: {until_dt.date()}")
        console.print(f"Max commits: {max_commits}")

        # Initialize Graphiti
        with console.status("[bold]Initializing Graphiti...[/bold]"):
            graphiti = Graphiti(neo4j_uri, neo4j_password, "neo4j")

        # Initialize integration
        integration = GitGraphitiIntegration(repository, graphiti)

        # Ingest git history
        async def run_ingestion():
            return await integration.ingest_git_history(
                since=since_dt,
                until=until_dt,
                branch=branch,
                max_commits=max_commits,
                batch_size=batch_size,
            )

        with console.status("[bold]Processing commits...[/bold]"):
            stats = asyncio.run(run_ingestion())

        # Display results
        console.print("\n[green]✓ Ingestion complete[/green]")
        console.print(f"  Commits processed: {stats['commits_processed']}")
        if stats['errors'] > 0:
            console.print(f"  [yellow]Errors: {stats['errors']}[/yellow]")
        if stats['oldest_commit']:
            console.print(f"  Date range: {stats['oldest_commit'].date()} to {stats['newest_commit'].date()}")

    except Exception as e:
        logger.error(f"Failed to ingest git history: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@historical.command()
@click.argument("query")
@click.argument("repository", type=click.Path(exists=True))
@click.option("--since", "-s", help="Filter results after this date (YYYY-MM-DD)")
@click.option("--until", "-u", help="Filter results before this date (YYYY-MM-DD)")
@click.option("--neo4j-uri", envvar="REPOTOIRE_NEO4J_URI", default="bolt://localhost:7687", help="Neo4j connection URI")
@click.option("--neo4j-password", envvar="REPOTOIRE_NEO4J_PASSWORD", help="Neo4j password")
@click.pass_context
def query(
    ctx: click.Context,
    query: str,
    repository: str,
    since: Optional[str],
    until: Optional[str],
    neo4j_uri: str,
    neo4j_password: Optional[str],
) -> None:
    """Query git history using natural language.

    Ask questions about code evolution, when features were added, who made changes,
    and other historical questions about the codebase.

    Examples:
        repotoire historical query "When did we add OAuth authentication?" /path/to/repo
        repotoire historical query "What changes led to performance regression?" /path/to/repo
        repotoire historical query "Show all refactorings of UserManager class" /path/to/repo
    """
    import asyncio
    from datetime import datetime, timezone

    try:
        # Check for required dependencies
        try:
            from graphiti_core import Graphiti
            from repotoire.historical import GitGraphitiIntegration
        except ImportError:
            console.print("\n[red]❌ Graphiti not installed[/red]")
            console.print(
                "[dim]Install with: uv pip install 'repotoire[graphiti]' or pip install graphiti-core[/dim]"
            )
            raise click.Abort()

        # Check for Neo4j password
        if not neo4j_password:
            console.print("\n[red]❌ Neo4j password not provided[/red]")
            console.print("[dim]Set REPOTOIRE_NEO4J_PASSWORD or use --neo4j-password[/dim]")
            raise click.Abort()

        # Parse dates if provided
        since_dt = None
        until_dt = None

        if since:
            try:
                since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                console.print(f"\n[red]❌ Invalid date format for --since: {since}[/red]")
                raise click.Abort()

        if until:
            try:
                until_dt = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                console.print(f"\n[red]❌ Invalid date format for --until: {until}[/red]")
                raise click.Abort()

        console.print(f"\n[bold]🔍 Querying Git History[/bold]")
        console.print(f"Query: {query}")

        # Initialize Graphiti
        with console.status("[bold]Querying Graphiti...[/bold]"):
            graphiti = Graphiti(neo4j_uri, neo4j_password, "neo4j")
            integration = GitGraphitiIntegration(repository, graphiti)

            # Run query
            async def run_query():
                return await integration.query_history(
                    query=query,
                    start_time=since_dt,
                    end_time=until_dt,
                )

            results = asyncio.run(run_query())

        # Display results
        console.print("\n[bold]Results:[/bold]")
        console.print(results)

    except Exception as e:
        logger.error(f"Failed to query git history: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@historical.command()
@click.argument("entity_name")
@click.argument("repository", type=click.Path(exists=True))
@click.option("--entity-type", "-t", default="function", help="Type of entity (function, class, module)")
@click.option("--neo4j-uri", envvar="REPOTOIRE_NEO4J_URI", default="bolt://localhost:7687", help="Neo4j connection URI")
@click.option("--neo4j-password", envvar="REPOTOIRE_NEO4J_PASSWORD", help="Neo4j password")
@click.pass_context
def timeline(
    ctx: click.Context,
    entity_name: str,
    repository: str,
    entity_type: str,
    neo4j_uri: str,
    neo4j_password: Optional[str],
) -> None:
    """Get timeline of changes for a specific code entity.

    Shows all commits that modified a particular function, class, or module
    over time, helping understand how that code evolved.

    Examples:
        repotoire historical timeline authenticate_user /path/to/repo --entity-type function
        repotoire historical timeline UserManager /path/to/repo --entity-type class
    """
    import asyncio

    try:
        # Check for required dependencies
        try:
            from graphiti_core import Graphiti
            from repotoire.historical import GitGraphitiIntegration
        except ImportError:
            console.print("\n[red]❌ Graphiti not installed[/red]")
            console.print(
                "[dim]Install with: uv pip install 'repotoire[graphiti]' or pip install graphiti-core[/dim]"
            )
            raise click.Abort()

        # Check for Neo4j password
        if not neo4j_password:
            console.print("\n[red]❌ Neo4j password not provided[/red]")
            console.print("[dim]Set REPOTOIRE_NEO4J_PASSWORD or use --neo4j-password[/dim]")
            raise click.Abort()

        console.print(f"\n[bold]📅 Timeline for {entity_type}: {entity_name}[/bold]")

        # Initialize Graphiti
        with console.status("[bold]Retrieving timeline...[/bold]"):
            graphiti = Graphiti(neo4j_uri, neo4j_password, "neo4j")
            integration = GitGraphitiIntegration(repository, graphiti)

            # Get timeline
            async def run_timeline():
                return await integration.get_entity_timeline(
                    entity_name=entity_name,
                    entity_type=entity_type,
                )

            results = asyncio.run(run_timeline())

        # Display results
        console.print("\n[bold]Timeline:[/bold]")
        console.print(results)

    except Exception as e:
        logger.error(f"Failed to get entity timeline: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


@cli.command("auto-fix")
@click.argument("repository", type=click.Path(exists=True))
@click.option("--max-fixes", "-n", type=int, default=10, help="Maximum fixes to generate")
@click.option("--severity", "-s", type=click.Choice(["critical", "high", "medium", "low"]), help="Minimum severity to fix")
@click.option("--auto-approve-high", is_flag=True, help="Auto-approve high-confidence fixes")
@click.option("--create-branch/--no-branch", default=True, help="Create git branch for fixes")
@click.option("--run-tests", is_flag=True, help="Run tests after applying fixes")
@click.option("--test-command", default="pytest", help="Test command to run")
@click.option("--neo4j-uri", envvar="REPOTOIRE_NEO4J_URI", default="bolt://localhost:7687", help="Neo4j connection URI")
@click.option("--neo4j-password", envvar="REPOTOIRE_NEO4J_PASSWORD", help="Neo4j password")
@click.pass_context
def auto_fix(
    ctx: click.Context,
    repository: str,
    max_fixes: int,
    severity: Optional[str],
    auto_approve_high: bool,
    create_branch: bool,
    run_tests: bool,
    test_command: str,
    neo4j_uri: str,
    neo4j_password: Optional[str],
) -> None:
    """AI-powered automatic code fixing with human-in-the-loop approval.

    Analyzes your codebase, generates AI-powered fixes, and presents them
    for interactive review. Approved fixes are automatically applied with
    git integration.

    Examples:
        # Generate and review up to 10 fixes
        repotoire auto-fix /path/to/repo

        # Auto-approve high-confidence fixes
        repotoire auto-fix /path/to/repo --auto-approve-high

        # Only fix critical issues
        repotoire auto-fix /path/to/repo --severity critical

        # Apply fixes and run tests
        repotoire auto-fix /path/to/repo --run-tests
    """
    import os
    from pathlib import Path
    from repotoire.graph import Neo4jClient
    from repotoire.engine import AnalysisEngine
    from repotoire.autofix import AutoFixEngine, InteractiveReviewer, FixApplicator
    from repotoire.models import Severity

    try:
        # Check for OpenAI API key
        if not os.getenv("OPENAI_API_KEY"):
            console.print("\n[red]❌ OPENAI_API_KEY not set[/red]")
            console.print("[dim]Auto-fix requires an OpenAI API key for fix generation[/dim]")
            raise click.Abort()

        # Check for Neo4j password
        if not neo4j_password:
            console.print("\n[red]❌ Neo4j password not provided[/red]")
            console.print("[dim]Set REPOTOIRE_NEO4J_PASSWORD or use --neo4j-password[/dim]")
            raise click.Abort()

        repo_path = Path(repository)

        console.print("\n[bold cyan]🤖 Repotoire Auto-Fix[/bold cyan]")
        console.print(f"Repository: {repository}\n")

        # Step 1: Analyze codebase
        console.print("[bold]Step 1: Analyzing codebase...[/bold]")

        neo4j_client = Neo4jClient(uri=neo4j_uri, password=neo4j_password)
        engine = AnalysisEngine(neo4j_client)

        with console.status("[bold]Running code analysis..."):
            health = engine.analyze(str(repo_path))

        findings = health.findings

        # Filter by severity if specified
        if severity:
            severity_enum = getattr(Severity, severity.upper())
            findings = [f for f in findings if f.severity == severity_enum]

        console.print(f"[green]✓[/green] Found {len(findings)} issue(s)")

        if not findings:
            console.print("\n[yellow]No issues found. Your code is clean! 🎉[/yellow]")
            neo4j_client.close()
            return

        # Limit to max fixes
        findings = findings[:max_fixes]
        console.print(f"[dim]Generating fixes for {len(findings)} issue(s)...[/dim]\n")

        # Step 2: Generate fixes
        console.print("[bold]Step 2: Generating AI-powered fixes...[/bold]")

        fix_engine = AutoFixEngine(neo4j_client)
        fix_proposals = []

        import asyncio

        async def generate_all_fixes():
            tasks = []
            for finding in findings:
                task = fix_engine.generate_fix(finding, repo_path)
                tasks.append(task)
            return await asyncio.gather(*tasks)

        with console.status(f"[bold]Generating {len(findings)} fix(es)..."):
            fixes = asyncio.run(generate_all_fixes())

        # Filter out failed generations
        fix_proposals = [f for f in fixes if f is not None]

        console.print(f"[green]✓[/green] Generated {len(fix_proposals)} fix proposal(s)\n")

        if not fix_proposals:
            console.print("[yellow]No fixes could be generated.[/yellow]")
            neo4j_client.close()
            return

        # Step 3: Interactive review
        console.print("[bold]Step 3: Reviewing fixes...[/bold]\n")

        reviewer = InteractiveReviewer(console)
        approved_fixes = reviewer.review_batch(fix_proposals, auto_approve_high=auto_approve_high)

        if not approved_fixes:
            console.print("\n[yellow]No fixes approved. Exiting.[/yellow]")
            neo4j_client.close()
            return

        # Step 4: Apply fixes
        console.print(f"\n[bold]Step 4: Applying {len(approved_fixes)} fix(es)...[/bold]")

        applicator = FixApplicator(repo_path, create_branch=create_branch)

        with console.status("[bold]Applying fixes..."):
            successful, failed = applicator.apply_batch(approved_fixes, commit_each=False)

        console.print(f"[green]✓[/green] Applied {len(successful)} fix(es)")

        if failed:
            console.print(f"[red]✗[/red] {len(failed)} fix(es) failed to apply:")
            for fix, error in failed:
                console.print(f"  - {fix.title}: {error}")

        # Step 5: Run tests if requested
        if run_tests and successful:
            console.print(f"\n[bold]Step 5: Running tests...[/bold]")

            with console.status(f"[bold]Running {test_command}..."):
                tests_passed, output = applicator.run_tests(test_command)

            if tests_passed:
                console.print("[green]✓[/green] All tests passed")
            else:
                console.print("[red]✗[/red] Tests failed")
                console.print("\n[dim]Test output:[/dim]")
                console.print(output[:1000])  # Show first 1000 chars

                # Offer rollback
                if Confirm.ask("\n[yellow]Tests failed. Rollback changes?[/yellow]", default=True):
                    applicator.rollback()
                    console.print("[green]✓[/green] Changes rolled back")

        # Summary
        reviewer.show_summary(
            total=len(fix_proposals),
            approved=len(approved_fixes),
            applied=len(successful),
            failed=len(failed),
        )

        neo4j_client.close()

    except Exception as e:
        logger.error(f"Auto-fix failed: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        raise click.Abort()


# Register security commands
from .security import security
cli.add_command(security)


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
