"""CLI commands for ML training data operations.

Provides commands for:
- Extracting training data from git history
- Viewing dataset statistics
- Interactive labeling with active learning
"""

import click
import json
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()


@click.group()
def ml():
    """Machine learning commands for training data extraction."""
    pass


@ml.command("extract-training-data")
@click.argument("repo_path", type=click.Path(exists=True))
@click.option(
    "--since",
    default="2020-01-01",
    help="Start date for commit history (YYYY-MM-DD, default: 2020-01-01)",
)
@click.option(
    "--output",
    "-o",
    default="training_data.json",
    help="Output file for training data (default: training_data.json)",
)
@click.option(
    "--max-examples",
    type=int,
    help="Maximum total examples to extract (will be balanced 50/50)",
)
@click.option(
    "--max-commits",
    type=int,
    help="Maximum commits to analyze (for faster testing)",
)
@click.option(
    "--keywords",
    "-k",
    multiple=True,
    help="Custom bug-fix keywords (can specify multiple, e.g., -k fix -k bug)",
)
@click.option(
    "--min-loc",
    type=int,
    default=5,
    help="Minimum lines of code for functions (default: 5)",
)
@click.option(
    "--include-source/--no-source",
    default=True,
    help="Include function source code in output (default: yes)",
)
def extract_training_data(
    repo_path: str,
    since: str,
    output: str,
    max_examples: Optional[int],
    max_commits: Optional[int],
    keywords: tuple,
    min_loc: int,
    include_source: bool,
):
    """Extract training data from git history for bug prediction.

    Analyzes commit history to identify functions changed in bug-fix commits
    (labeled as 'buggy') vs functions never involved in bugs ('clean').

    Examples:

        # Basic extraction
        repotoire ml extract-training-data /path/to/repo

        # Limit to recent commits
        repotoire ml extract-training-data /path/to/repo --since 2023-01-01

        # Custom output and limits
        repotoire ml extract-training-data ./myrepo -o data.json --max-examples 1000

        # Custom keywords
        repotoire ml extract-training-data ./myrepo -k fix -k defect -k regression
    """
    from repotoire.ml.training_data import GitBugLabelExtractor

    console.print(f"[bold blue]Extracting training data from {repo_path}[/bold blue]")
    console.print(f"[dim]Analyzing commits since {since}[/dim]\n")

    try:
        # Initialize extractor
        custom_keywords = list(keywords) if keywords else None
        extractor = GitBugLabelExtractor(
            Path(repo_path),
            keywords=custom_keywords,
            min_loc=min_loc,
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            # Step 1: Extract buggy functions
            task = progress.add_task("Mining git history for bug fixes...", total=None)
            buggy = extractor.extract_buggy_functions(
                since_date=since,
                max_commits=max_commits,
            )
            progress.update(task, description=f"Found {len(buggy)} buggy functions")

            # Step 2: Scan codebase
            progress.update(task, description="Scanning codebase for clean functions...")
            all_funcs = extractor._scan_all_functions()
            progress.update(
                task,
                description=f"Scanned {len(all_funcs)} total functions",
            )

            # Step 3: Create balanced dataset
            progress.update(task, description="Creating balanced dataset...")
            dataset = extractor.create_balanced_dataset(
                since_date=since,
                max_examples=max_examples,
            )

            # Step 4: Optionally strip source code
            if not include_source:
                for ex in dataset.examples:
                    ex.source_code = None

            progress.update(task, description="Saving dataset...")

        # Save to JSON
        output_path = Path(output)
        extractor.export_to_json(dataset, output_path)

        console.print(f"\n[green]Saved {len(dataset.examples)} examples to {output}[/green]")

        # Print statistics
        _print_stats(dataset)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Training data extraction failed")
        raise click.Abort()


@ml.command("training-stats")
@click.argument("dataset_path", type=click.Path(exists=True))
@click.option(
    "--detailed/--summary",
    default=False,
    help="Show detailed per-file statistics",
)
def training_stats(dataset_path: str, detailed: bool):
    """Display statistics for a training dataset.

    Shows label distribution, complexity metrics, and coverage information.

    Examples:

        repotoire ml training-stats training_data.json
        repotoire ml training-stats data.json --detailed
    """
    from repotoire.ml.training_data import TrainingDataset

    try:
        with open(dataset_path) as f:
            data = json.load(f)

        dataset = TrainingDataset(**data)
        _print_stats(dataset)

        if detailed:
            _print_detailed_stats(dataset)

    except Exception as e:
        console.print(f"[red]Error loading dataset: {e}[/red]")
        raise click.Abort()


@ml.command("label")
@click.argument("dataset_path", type=click.Path(exists=True))
@click.option(
    "--samples",
    default=20,
    type=int,
    help="Number of samples to label per iteration (default: 20)",
)
@click.option(
    "--iterations",
    default=1,
    type=int,
    help="Number of active learning iterations (default: 1)",
)
@click.option(
    "--show-source/--no-source",
    default=True,
    help="Show function source code during labeling",
)
@click.option(
    "--export-labels",
    type=click.Path(),
    help="Export labels to separate file after session",
)
@click.option(
    "--import-labels",
    type=click.Path(exists=True),
    help="Import previously saved labels before starting",
)
def label(
    dataset_path: str,
    samples: int,
    iterations: int,
    show_source: bool,
    export_labels: Optional[str],
    import_labels: Optional[str],
):
    """Interactive labeling with active learning.

    Presents uncertain samples for human review to improve label quality.
    Uses uncertainty sampling to prioritize samples where the model is
    least confident.

    Examples:

        # Basic interactive labeling
        repotoire ml label training_data.json

        # Multiple iterations with more samples
        repotoire ml label data.json --iterations 3 --samples 30

        # Continue from previous session
        repotoire ml label data.json --import-labels previous_labels.json
    """
    from repotoire.ml.training_data import TrainingDataset, ActiveLearningLabeler

    try:
        # Check for questionary
        try:
            import questionary
        except ImportError:
            console.print(
                "[red]Interactive labeling requires questionary package.[/red]"
            )
            console.print("[yellow]Install with: pip install questionary[/yellow]")
            raise click.Abort()

        # Load dataset
        with open(dataset_path) as f:
            data = json.load(f)

        dataset = TrainingDataset(**data)
        console.print(
            f"[bold blue]Loaded dataset with {len(dataset.examples)} examples[/bold blue]\n"
        )

        # Initialize labeler
        labeler = ActiveLearningLabeler()

        # Import previous labels if provided
        if import_labels:
            imported = labeler.import_labels(Path(import_labels))
            console.print(f"[green]Imported {len(imported)} previous labels[/green]\n")

        # Run active learning
        if iterations > 1:
            dataset = labeler.iterative_training(
                dataset,
                n_iterations=iterations,
                samples_per_iteration=samples,
            )
        else:
            # Single iteration - just select and label
            low_confidence = [ex for ex in dataset.examples if ex.confidence < 1.0]
            uncertain = labeler.select_uncertain_samples(low_confidence, n_samples=samples)
            labeler.label_samples_interactively(uncertain, show_source=show_source)

        # Save updated dataset
        with open(dataset_path, "w") as f:
            json.dump(dataset.model_dump(), f, indent=2)

        console.print(f"\n[green]Updated {dataset_path} with human labels[/green]")

        # Print labeling stats
        stats = labeler.get_labeling_stats()
        console.print(f"[cyan]Session stats:[/cyan]")
        console.print(f"  Total labeled: {stats['total_labeled']}")
        console.print(f"  Buggy: {stats['buggy_count']}")
        console.print(f"  Clean: {stats['clean_count']}")

        # Export labels if requested
        if export_labels:
            labeler.export_labels(Path(export_labels))
            console.print(f"\n[green]Exported labels to {export_labels}[/green]")

    except click.Abort:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Labeling failed")
        raise click.Abort()


@ml.command("validate-dataset")
@click.argument("dataset_path", type=click.Path(exists=True))
@click.option(
    "--check-duplicates/--no-check-duplicates",
    default=True,
    help="Check for duplicate function names",
)
@click.option(
    "--check-balance/--no-check-balance",
    default=True,
    help="Check label balance",
)
@click.option(
    "--fix/--no-fix",
    default=False,
    help="Attempt to fix issues (removes duplicates, rebalances)",
)
def validate_dataset(
    dataset_path: str,
    check_duplicates: bool,
    check_balance: bool,
    fix: bool,
):
    """Validate training dataset for quality issues.

    Checks for duplicates, label imbalance, and data quality issues.

    Examples:

        repotoire ml validate-dataset training_data.json
        repotoire ml validate-dataset data.json --fix
    """
    from repotoire.ml.training_data import TrainingDataset

    try:
        with open(dataset_path) as f:
            data = json.load(f)

        dataset = TrainingDataset(**data)
        issues = []
        fixed = []

        console.print(f"[bold blue]Validating {dataset_path}[/bold blue]\n")

        # Check duplicates
        if check_duplicates:
            seen = {}
            duplicates = []
            for ex in dataset.examples:
                if ex.qualified_name in seen:
                    duplicates.append(ex.qualified_name)
                else:
                    seen[ex.qualified_name] = ex

            if duplicates:
                issues.append(f"Found {len(duplicates)} duplicate function names")
                if fix:
                    dataset.examples = list(seen.values())
                    fixed.append(f"Removed {len(duplicates)} duplicates")

        # Check balance
        if check_balance:
            buggy = sum(1 for ex in dataset.examples if ex.label == "buggy")
            clean = sum(1 for ex in dataset.examples if ex.label == "clean")
            total = len(dataset.examples)

            if total > 0:
                buggy_pct = buggy / total * 100
                if abs(buggy_pct - 50) > 10:
                    issues.append(
                        f"Label imbalance: {buggy_pct:.1f}% buggy (target: 50%)"
                    )

        # Check confidence
        low_confidence = sum(1 for ex in dataset.examples if ex.confidence < 1.0)
        if low_confidence > len(dataset.examples) * 0.5:
            issues.append(
                f"{low_confidence} examples ({low_confidence/len(dataset.examples)*100:.1f}%) "
                "have low confidence - consider human labeling"
            )

        # Print results
        if issues:
            console.print("[yellow]Issues found:[/yellow]")
            for issue in issues:
                console.print(f"  [yellow]{issue}[/yellow]")
        else:
            console.print("[green]No issues found![/green]")

        if fixed:
            console.print("\n[green]Fixed:[/green]")
            for fix_msg in fixed:
                console.print(f"  [green]{fix_msg}[/green]")

            # Save fixed dataset
            with open(dataset_path, "w") as f:
                json.dump(dataset.model_dump(), f, indent=2)
            console.print(f"\n[green]Saved fixed dataset to {dataset_path}[/green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@ml.command("merge-datasets")
@click.argument("output_path", type=click.Path())
@click.argument("dataset_paths", type=click.Path(exists=True), nargs=-1)
@click.option(
    "--deduplicate/--allow-duplicates",
    default=True,
    help="Remove duplicate functions (default: deduplicate)",
)
def merge_datasets(
    output_path: str,
    dataset_paths: tuple,
    deduplicate: bool,
):
    """Merge multiple training datasets into one.

    Combines examples from multiple dataset files, optionally deduplicating.

    Examples:

        repotoire ml merge-datasets combined.json data1.json data2.json data3.json
    """
    from repotoire.ml.training_data import TrainingDataset
    from datetime import datetime

    if len(dataset_paths) < 2:
        console.print("[red]Need at least 2 datasets to merge[/red]")
        raise click.Abort()

    try:
        all_examples = []
        repositories = set()
        earliest_date = None
        latest_date = None

        for path in dataset_paths:
            with open(path) as f:
                data = json.load(f)
            ds = TrainingDataset(**data)
            all_examples.extend(ds.examples)
            repositories.add(ds.repository)

            # Track date ranges
            start, end = ds.date_range
            if earliest_date is None or start < earliest_date:
                earliest_date = start
            if latest_date is None or end > latest_date:
                latest_date = end

        console.print(
            f"[blue]Merging {len(dataset_paths)} datasets "
            f"({len(all_examples)} total examples)[/blue]"
        )

        # Deduplicate
        if deduplicate:
            seen = {}
            for ex in all_examples:
                # Prefer higher confidence examples
                if ex.qualified_name not in seen or ex.confidence > seen[ex.qualified_name].confidence:
                    seen[ex.qualified_name] = ex
            all_examples = list(seen.values())
            console.print(f"[dim]After deduplication: {len(all_examples)} examples[/dim]")

        # Calculate stats
        buggy = sum(1 for ex in all_examples if ex.label == "buggy")
        clean = sum(1 for ex in all_examples if ex.label == "clean")
        total = len(all_examples)

        stats = {
            "total": total,
            "buggy": buggy,
            "clean": clean,
            "buggy_pct": round(buggy / total * 100, 1) if total > 0 else 0,
            "source_datasets": len(dataset_paths),
            "source_repositories": len(repositories),
        }

        # Create merged dataset
        merged = TrainingDataset(
            examples=all_examples,
            repository=", ".join(sorted(repositories)),
            extracted_at=datetime.now().isoformat(),
            date_range=(earliest_date or "", latest_date or ""),
            statistics=stats,
        )

        # Save
        with open(output_path, "w") as f:
            json.dump(merged.model_dump(), f, indent=2)

        console.print(f"\n[green]Merged dataset saved to {output_path}[/green]")
        _print_stats(merged)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


def _print_stats(dataset) -> None:
    """Print dataset statistics in a formatted table."""
    from repotoire.ml.training_data import TrainingDataset

    table = Table(title="Training Data Statistics", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")

    stats = dataset.statistics
    table.add_row("Total functions", str(stats.get("total", len(dataset.examples))))
    table.add_row("Buggy", f"{stats.get('buggy', 0)} ({stats.get('buggy_pct', 0)}%)")
    table.add_row("Clean", f"{stats.get('clean', 0)} ({100 - stats.get('buggy_pct', 0)}%)")

    if "avg_complexity" in stats:
        table.add_row("Avg complexity", f"{stats['avg_complexity']:.1f}")
    if "avg_loc" in stats:
        table.add_row("Avg LOC", f"{stats['avg_loc']:.1f}")
    if "human_labeled" in stats:
        table.add_row("Human-labeled", str(stats["human_labeled"]))

    table.add_row("Date range", f"{dataset.date_range[0]} to {dataset.date_range[1]}")
    table.add_row("Repository", dataset.repository[:60] + "..." if len(dataset.repository) > 60 else dataset.repository)

    console.print(table)


def _print_detailed_stats(dataset) -> None:
    """Print detailed per-file statistics."""
    console.print("\n[bold]Per-file breakdown:[/bold]\n")

    # Group by file
    by_file = {}
    for ex in dataset.examples:
        if ex.file_path not in by_file:
            by_file[ex.file_path] = {"buggy": 0, "clean": 0}
        by_file[ex.file_path][ex.label] += 1

    # Sort by total count
    sorted_files = sorted(
        by_file.items(),
        key=lambda x: x[1]["buggy"] + x[1]["clean"],
        reverse=True,
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("File", style="dim")
    table.add_column("Buggy", justify="right", style="red")
    table.add_column("Clean", justify="right", style="green")
    table.add_column("Total", justify="right")

    for file_path, counts in sorted_files[:20]:  # Top 20 files
        table.add_row(
            file_path[:50] + "..." if len(file_path) > 50 else file_path,
            str(counts["buggy"]),
            str(counts["clean"]),
            str(counts["buggy"] + counts["clean"]),
        )

    if len(sorted_files) > 20:
        table.add_row("...", "...", "...", f"(+{len(sorted_files) - 20} more files)")

    console.print(table)

    # Complexity distribution
    console.print("\n[bold]Complexity distribution:[/bold]")
    complexities = [ex.complexity for ex in dataset.examples if ex.complexity]
    if complexities:
        console.print(f"  Min: {min(complexities)}")
        console.print(f"  Max: {max(complexities)}")
        console.print(f"  Median: {sorted(complexities)[len(complexities)//2]}")
