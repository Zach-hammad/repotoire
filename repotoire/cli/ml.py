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


# ============================================================================
# Node2Vec Embedding Commands
# ============================================================================


@ml.command("generate-embeddings")
@click.argument("repo_path", type=click.Path(exists=True), required=False, default=".")
@click.option(
    "--type",
    "embedding_type",
    default="node2vec",
    type=click.Choice(["node2vec"]),
    help="Embedding algorithm (default: node2vec)",
)
@click.option(
    "--dimension",
    default=128,
    type=int,
    help="Embedding dimension (default: 128)",
)
@click.option(
    "--walk-length",
    default=80,
    type=int,
    help="Random walk length (default: 80)",
)
@click.option(
    "--walks-per-node",
    default=10,
    type=int,
    help="Number of walks per node (default: 10)",
)
@click.option(
    "--return-factor",
    "return_factor",
    default=1.0,
    type=float,
    help="Return factor p - controls BFS vs DFS behavior (default: 1.0)",
)
@click.option(
    "--in-out-factor",
    "in_out_factor",
    default=1.0,
    type=float,
    help="In-out factor q - controls explore vs exploit (default: 1.0)",
)
@click.option(
    "--node-types",
    default="Function,Class,Module",
    help="Comma-separated node types to include (default: Function,Class,Module)",
)
@click.option(
    "--relationship-types",
    default="CALLS,IMPORTS,USES",
    help="Comma-separated relationship types (default: CALLS,IMPORTS,USES)",
)
def generate_embeddings(
    repo_path: str,
    embedding_type: str,
    dimension: int,
    walk_length: int,
    walks_per_node: int,
    return_factor: float,
    in_out_factor: float,
    node_types: str,
    relationship_types: str,
):
    """Generate Node2Vec embeddings for code graph nodes.

    Creates graph embeddings using random walks that capture both local
    (BFS-like) and global (DFS-like) structural patterns in the call graph.

    Prerequisites:
    - Codebase must be ingested first (repotoire ingest)
    - Neo4j with GDS plugin must be running

    Examples:

        # Basic embedding generation
        repotoire ml generate-embeddings

        # Custom parameters
        repotoire ml generate-embeddings --dimension 256 --walks-per-node 20

        # BFS-biased walks (tight communities)
        repotoire ml generate-embeddings --return-factor 0.5 --in-out-factor 2.0

        # DFS-biased walks (structural roles)
        repotoire ml generate-embeddings --return-factor 2.0 --in-out-factor 0.5
    """
    from repotoire.ml.node2vec_embeddings import Node2VecEmbedder, Node2VecConfig
    from repotoire.graph.client import Neo4jClient

    console.print(f"[bold blue]Generating {embedding_type} embeddings[/bold blue]")
    console.print(f"[dim]Dimension: {dimension}, Walk length: {walk_length}[/dim]\n")

    try:
        client = Neo4jClient.from_env()
        config = Node2VecConfig(
            embedding_dimension=dimension,
            walk_length=walk_length,
            walks_per_node=walks_per_node,
            return_factor=return_factor,
            in_out_factor=in_out_factor,
        )

        embedder = Node2VecEmbedder(client, config)

        # Parse node/relationship types
        node_label_list = [n.strip() for n in node_types.split(",")]
        rel_type_list = [r.strip() for r in relationship_types.split(",")]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            # Step 1: Create projection
            task = progress.add_task("Creating graph projection...", total=None)
            try:
                proj_stats = embedder.create_projection(
                    node_labels=node_label_list,
                    relationship_types=rel_type_list,
                )
                progress.update(
                    task,
                    description=f"Projected {proj_stats.get('nodeCount', 0)} nodes, "
                    f"{proj_stats.get('relationshipCount', 0)} relationships",
                )
            except RuntimeError as e:
                console.print(f"[red]Error: {e}[/red]")
                console.print(
                    "[yellow]Make sure Neo4j GDS plugin is installed, or use FalkorDB.[/yellow]"
                )
                raise click.Abort()

            # Step 2: Generate embeddings
            progress.update(task, description="Running Node2Vec algorithm...")
            embed_stats = embedder.generate_embeddings()

            progress.update(
                task,
                description=f"Generated {embed_stats.get('nodePropertiesWritten', 0)} embeddings "
                f"in {embed_stats.get('computeMillis', 0)}ms",
            )

            # Step 3: Cleanup
            progress.update(task, description="Cleaning up projection...")
            embedder.cleanup()

        # Print statistics
        stats = embedder.compute_embedding_statistics(node_type="Function")

        table = Table(title="Embedding Statistics", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")

        table.add_row("Nodes with embeddings", str(stats.get("count", 0)))
        table.add_row("Embedding dimension", str(stats.get("dimension", dimension)))
        table.add_row("Mean L2 norm", f"{stats.get('mean_norm', 0):.4f}")
        table.add_row("Std L2 norm", f"{stats.get('std_norm', 0):.4f}")
        table.add_row("Compute time (ms)", str(embed_stats.get("computeMillis", 0)))

        console.print(table)
        console.print("\n[green]Embeddings generated successfully![/green]")
        console.print("[dim]Embeddings stored as 'node2vec_embedding' property on nodes[/dim]")

    except click.Abort:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Embedding generation failed")
        raise click.Abort()


# ============================================================================
# Bug Prediction Commands
# ============================================================================


@ml.command("train-bug-predictor")
@click.option(
    "--training-data",
    "-d",
    required=True,
    type=click.Path(exists=True),
    help="Path to training data JSON file",
)
@click.option(
    "--output",
    "-o",
    default="models/bug_predictor.pkl",
    help="Output path for trained model (default: models/bug_predictor.pkl)",
)
@click.option(
    "--test-split",
    default=0.2,
    type=float,
    help="Fraction of data for testing (default: 0.2)",
)
@click.option(
    "--cv-folds",
    default=5,
    type=int,
    help="Number of cross-validation folds (default: 5)",
)
@click.option(
    "--grid-search/--no-grid-search",
    default=False,
    help="Run hyperparameter tuning with GridSearchCV",
)
@click.option(
    "--n-estimators",
    default=100,
    type=int,
    help="Number of trees in RandomForest (default: 100)",
)
@click.option(
    "--max-depth",
    default=10,
    type=int,
    help="Maximum tree depth (default: 10)",
)
def train_bug_predictor(
    training_data: str,
    output: str,
    test_split: float,
    cv_folds: int,
    grid_search: bool,
    n_estimators: int,
    max_depth: int,
):
    """Train bug prediction model on labeled training data.

    Trains a RandomForest classifier using Node2Vec embeddings combined
    with code metrics (complexity, LOC, coupling) to predict bug probability.

    Prerequisites:
    - Training data extracted with 'repotoire ml extract-training-data'
    - Node2Vec embeddings generated with 'repotoire ml generate-embeddings'

    Examples:

        # Basic training
        repotoire ml train-bug-predictor -d training_data.json

        # With hyperparameter search
        repotoire ml train-bug-predictor -d data.json --grid-search -o models/tuned.pkl

        # Custom parameters
        repotoire ml train-bug-predictor -d data.json --n-estimators 200 --max-depth 15
    """
    from repotoire.ml.bug_predictor import BugPredictor, BugPredictorConfig
    from repotoire.ml.training_data import TrainingDataset
    from repotoire.graph.client import Neo4jClient

    console.print("[bold blue]Training bug prediction model[/bold blue]\n")

    try:
        # Load training data
        with open(training_data) as f:
            data = json.load(f)
        dataset = TrainingDataset(**data)

        console.print(f"[dim]Training examples: {len(dataset.examples)}[/dim]")
        buggy_count = sum(1 for ex in dataset.examples if ex.label == "buggy")
        console.print(f"[dim]Buggy: {buggy_count}, Clean: {len(dataset.examples) - buggy_count}[/dim]\n")

        # Initialize predictor
        client = Neo4jClient.from_env()
        config = BugPredictorConfig(
            n_estimators=n_estimators,
            max_depth=max_depth,
            test_split=test_split,
            cv_folds=cv_folds,
        )
        predictor = BugPredictor(client, config)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Training model...", total=None)

            if grid_search:
                progress.update(task, description="Running hyperparameter grid search...")

            metrics = predictor.train(dataset, hyperparameter_search=grid_search)

            progress.update(task, description="Model trained successfully")

        # Print metrics
        table = Table(title="Model Evaluation Metrics", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")

        metrics_dict = metrics.to_dict()
        for key, value in metrics_dict.items():
            if key in ("accuracy", "precision", "recall", "f1_score", "auc_roc"):
                table.add_row(key.replace("_", " ").title(), f"{value:.4f}")
            elif key == "cv_mean":
                table.add_row("CV Mean (AUC-ROC)", f"{value:.4f}")
            elif key == "cv_std":
                table.add_row("CV Std Dev", f"{value:.4f}")

        console.print(table)

        # Print feature importance
        importance = predictor.get_feature_importance_report()
        if importance:
            console.print("\n[bold]Feature Importance:[/bold]")
            console.print(f"  Embeddings total: {importance.get('embedding_total', 0):.2%}")
            for name in ["complexity", "loc", "fan_in", "fan_out", "churn"]:
                if name in importance:
                    console.print(f"  {name}: {importance[name]:.2%}")

        # Save model
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        predictor.save(output_path)

        console.print(f"\n[green]Model saved to {output}[/green]")

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print(
            "[yellow]Ensure Node2Vec embeddings are generated first: "
            "repotoire ml generate-embeddings[/yellow]"
        )
        raise click.Abort()
    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[yellow]Install ML dependencies: pip install scikit-learn joblib[/yellow]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Training failed")
        raise click.Abort()


@ml.command("predict-bugs")
@click.argument("repo_path", type=click.Path(exists=True), required=False, default=".")
@click.option(
    "--model",
    "-m",
    required=True,
    type=click.Path(exists=True),
    help="Path to trained model file",
)
@click.option(
    "--threshold",
    default=0.7,
    type=float,
    help="Risk threshold for flagging (0.0-1.0, default: 0.7)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output JSON file for predictions",
)
@click.option(
    "--top-n",
    default=20,
    type=int,
    help="Show top N risky functions (default: 20)",
)
@click.option(
    "--function",
    "-f",
    "single_function",
    type=str,
    help="Predict for a single function by qualified name",
)
def predict_bugs(
    repo_path: str,
    model: str,
    threshold: float,
    output: Optional[str],
    top_n: int,
    single_function: Optional[str],
):
    """Predict bug-prone functions using trained model.

    Uses a trained bug prediction model to identify functions with high
    probability of containing bugs based on structural patterns and metrics.

    Examples:

        # Predict all functions
        repotoire ml predict-bugs -m models/bug_predictor.pkl

        # Export results to JSON
        repotoire ml predict-bugs -m model.pkl -o predictions.json

        # Show more results
        repotoire ml predict-bugs -m model.pkl --top-n 50

        # Predict single function
        repotoire ml predict-bugs -m model.pkl -f mymodule.MyClass.risky_method
    """
    from repotoire.ml.bug_predictor import BugPredictor
    from repotoire.graph.client import Neo4jClient

    console.print("[bold blue]Predicting bug-prone functions[/bold blue]\n")

    try:
        client = Neo4jClient.from_env()
        predictor = BugPredictor.load(Path(model), client)

        # Show model info
        if predictor.metrics:
            console.print(
                f"[dim]Model AUC-ROC: {predictor.metrics.auc_roc:.3f}, "
                f"Threshold: {threshold:.0%}[/dim]\n"
            )

        # Single function prediction
        if single_function:
            result = predictor.predict(single_function, risk_threshold=threshold)
            if result is None:
                console.print(f"[yellow]Function not found: {single_function}[/yellow]")
                console.print("[dim]Make sure Node2Vec embeddings are generated.[/dim]")
                raise click.Abort()

            _print_single_prediction(result)
            return

        # Batch prediction
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running predictions...", total=None)
            predictions = predictor.predict_all_functions(risk_threshold=threshold)
            progress.update(
                task,
                description=f"Analyzed {len(predictions)} functions",
            )

        # Sort by probability
        predictions.sort(key=lambda p: p.bug_probability, reverse=True)

        # Filter high-risk only
        high_risk = [p for p in predictions if p.is_high_risk]

        # Display results
        console.print(f"[bold]Found {len(high_risk)} high-risk functions[/bold]\n")

        table = Table(title=f"Top {min(top_n, len(high_risk))} Bug-Prone Functions")
        table.add_column("Function", style="cyan", max_width=50)
        table.add_column("File", style="dim", max_width=30)
        table.add_column("Probability", style="red", justify="right")
        table.add_column("Top Factor", style="yellow", max_width=25)

        for pred in high_risk[:top_n]:
            factor = pred.contributing_factors[0].split(" (")[0] if pred.contributing_factors else "-"
            # Color probability based on severity
            prob_style = "red" if pred.bug_probability >= 0.9 else "yellow"
            table.add_row(
                pred.qualified_name.split(".")[-1],
                pred.file_path.split("/")[-1] if "/" in pred.file_path else pred.file_path,
                f"[{prob_style}]{pred.bug_probability:.1%}[/{prob_style}]",
                factor,
            )

        console.print(table)

        # Summary
        console.print(f"\n[dim]Total functions analyzed: {len(predictions)}[/dim]")
        console.print(f"[dim]High-risk (>={threshold:.0%}): {len(high_risk)}[/dim]")

        # Save to JSON if requested
        if output:
            predictor.export_predictions(predictions, Path(output))
            console.print(f"\n[green]Predictions saved to {output}[/green]")

    except FileNotFoundError:
        console.print(f"[red]Model file not found: {model}[/red]")
        raise click.Abort()
    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Prediction failed")
        raise click.Abort()


def _print_single_prediction(pred) -> None:
    """Print detailed prediction for a single function."""
    # Severity color
    if pred.bug_probability >= 0.9:
        prob_style = "red bold"
        severity = "CRITICAL"
    elif pred.bug_probability >= 0.8:
        prob_style = "red"
        severity = "HIGH"
    elif pred.bug_probability >= 0.7:
        prob_style = "yellow"
        severity = "MEDIUM"
    else:
        prob_style = "green"
        severity = "LOW"

    console.print(Panel(
        f"[bold]{pred.qualified_name}[/bold]\n"
        f"File: {pred.file_path}\n\n"
        f"Bug Probability: [{prob_style}]{pred.bug_probability:.1%}[/{prob_style}] ({severity})\n"
        f"High Risk: {'Yes' if pred.is_high_risk else 'No'}",
        title="Bug Prediction Result",
        border_style="cyan",
    ))

    if pred.contributing_factors:
        console.print("\n[bold]Contributing Factors:[/bold]")
        for factor in pred.contributing_factors:
            console.print(f"  {factor}")

    if pred.similar_buggy_functions:
        console.print("\n[bold]Similar Past Buggy Functions:[/bold]")
        for similar in pred.similar_buggy_functions:
            console.print(f"  {similar}")
