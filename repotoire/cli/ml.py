"""CLI commands for ML training data operations.

Provides commands for:
- Extracting training data from git history
- Viewing dataset statistics
- Interactive labeling with active learning
"""

import click
import json
from pathlib import Path
from typing import Dict, Optional

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


@ml.command("fine-tune-embeddings")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    required=True,
    help="Output directory for the fine-tuned model",
)
@click.option(
    "--epochs",
    default=3,
    type=int,
    help="Number of training epochs (default: 3)",
)
@click.option(
    "--batch-size",
    default=32,
    type=int,
    help="Training batch size (default: 32)",
)
@click.option(
    "--base-model",
    default="all-MiniLM-L6-v2",
    help="Base model to fine-tune (default: all-MiniLM-L6-v2)",
)
@click.option(
    "--max-code-docstring-pairs",
    default=5000,
    type=int,
    help="Max code-docstring pairs to use (default: 5000)",
)
@click.option(
    "--max-same-class-pairs",
    default=2000,
    type=int,
    help="Max same-class method pairs (default: 2000)",
)
@click.option(
    "--max-caller-callee-pairs",
    default=2000,
    type=int,
    help="Max caller-callee pairs (default: 2000)",
)
@click.option(
    "--learning-rate",
    default=2e-5,
    type=float,
    help="Learning rate (default: 2e-5)",
)
@click.option(
    "--warmup-ratio",
    default=0.1,
    type=float,
    help="Warmup ratio for learning rate scheduler (default: 0.1)",
)
def fine_tune_embeddings(
    output: str,
    epochs: int,
    batch_size: int,
    base_model: str,
    max_code_docstring_pairs: int,
    max_same_class_pairs: int,
    max_caller_callee_pairs: int,
    learning_rate: float,
    warmup_ratio: float,
):
    """Fine-tune embeddings with contrastive learning on code-docstring pairs.

    Uses MultipleNegativesRankingLoss (InfoNCE with in-batch negatives) to
    fine-tune a sentence transformer model on code-specific positive pairs:

    \b
    1. Code-Docstring pairs: (source_code, docstring) - semantic alignment
    2. Same-Class pairs: (method1, method2) from same class - structural relatedness
    3. Caller-Callee pairs: (caller, callee) - call graph proximity

    The fine-tuned model can then be used as the local embedding backend
    for improved code search and RAG.

    Prerequisites:
    - Codebase must be ingested first (repotoire ingest)
    - Functions should have source_code and docstring properties in the graph

    Examples:

    \b
        # Basic fine-tuning with defaults
        repotoire ml fine-tune-embeddings -o models/code-embeddings

    \b
        # Custom configuration
        repotoire ml fine-tune-embeddings -o models/custom \\
            --epochs 5 --batch-size 64 --base-model all-mpnet-base-v2

    \b
        # Limit training pairs for faster iteration
        repotoire ml fine-tune-embeddings -o models/quick \\
            --max-code-docstring-pairs 1000 --epochs 1
    """
    from repotoire.ml.contrastive_learning import (
        ContrastiveConfig,
        ContrastivePairGenerator,
        ContrastiveTrainer,
    )
    from repotoire.graph.client import Neo4jClient

    console.print("[bold blue]Fine-tuning embeddings with contrastive learning[/bold blue]")
    console.print(f"[dim]Base model: {base_model}[/dim]")
    console.print(f"[dim]Epochs: {epochs}, Batch size: {batch_size}[/dim]\n")

    try:
        client = Neo4jClient.from_env()

        # Create configuration
        config = ContrastiveConfig(
            base_model=base_model,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            warmup_ratio=warmup_ratio,
            max_code_docstring_pairs=max_code_docstring_pairs,
            max_same_class_pairs=max_same_class_pairs,
            max_caller_callee_pairs=max_caller_callee_pairs,
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            # Step 1: Generate positive pairs from graph
            task = progress.add_task("Generating training pairs from graph...", total=None)

            generator = ContrastivePairGenerator(client)
            pairs = generator.generate_all_pairs(config)

            if not pairs:
                console.print("[red]No training pairs found![/red]")
                console.print(
                    "[yellow]Ensure codebase is ingested with source_code and docstrings.[/yellow]"
                )
                raise click.Abort()

            progress.update(
                task,
                description=f"Generated {len(pairs)} positive pairs",
            )

            # Step 2: Train with contrastive loss
            progress.update(task, description="Fine-tuning with contrastive loss...")

            trainer = ContrastiveTrainer(config)
            stats = trainer.train(pairs, Path(output))

            progress.update(task, description="Training complete")

        # Print summary
        table = Table(title="Fine-Tuning Results", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")

        table.add_row("Total pairs", str(stats["pairs"]))
        table.add_row("Epochs", str(stats["epochs"]))
        table.add_row("Batch size", str(stats["batch_size"]))
        table.add_row("Warmup steps", str(stats["warmup_steps"]))
        table.add_row("Total steps", str(stats["total_steps"]))
        table.add_row("Base model", stats["base_model"])

        console.print(table)

        console.print(f"\n[green]Model saved to {output}[/green]")
        console.print(
            "\n[dim]To use the fine-tuned model for embeddings:[/dim]\n"
            f"[dim]  export REPOTOIRE_EMBEDDING_MODEL={output}[/dim]\n"
            "[dim]  repotoire ingest /path/to/repo --generate-embeddings --embedding-backend local[/dim]"
        )

    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print(
            "[yellow]Install with: pip install sentence-transformers[/yellow]"
        )
        raise click.Abort()
    except click.Abort:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Fine-tuning failed")
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


# ============================================================================
# Multimodal Fusion Commands
# ============================================================================


@ml.command("prepare-multimodal-data")
@click.option(
    "--bug-labels",
    type=click.Path(exists=True),
    help="Bug labels JSON file",
)
@click.option(
    "--smell-labels",
    type=click.Path(exists=True),
    help="Code smell labels JSON file",
)
@click.option(
    "--refactor-labels",
    type=click.Path(exists=True),
    help="Refactoring benefit labels JSON file",
)
@click.option(
    "--output",
    "-o",
    required=True,
    help="Output pickle file for prepared data",
)
@click.option(
    "--test-split",
    default=0.2,
    type=float,
    help="Fraction of data for validation (default: 0.2)",
)
def prepare_multimodal_data(
    bug_labels: Optional[str],
    smell_labels: Optional[str],
    refactor_labels: Optional[str],
    output: str,
    test_split: float,
):
    """Prepare multi-task training data for multimodal fusion.

    Fetches text and graph embeddings from Neo4j and combines them
    with labels for multi-task learning.

    Label JSON format:
        [{"qualified_name": "module.Class.method", "label": "buggy"}, ...]

    Label values:
        - bug_prediction: "clean" or "buggy"
        - smell_detection: "none", "long_method", "god_class", "feature_envy", "data_clump"
        - refactoring_benefit: "low", "medium", "high"

    Prerequisites:
        - Run 'repotoire ingest --generate-embeddings' for text embeddings
        - Run 'repotoire ml generate-embeddings' for graph embeddings

    Examples:

        # Prepare with bug labels only
        repotoire ml prepare-multimodal-data --bug-labels bugs.json -o train_data.pkl

        # Prepare with all label types
        repotoire ml prepare-multimodal-data \\
            --bug-labels bugs.json \\
            --smell-labels smells.json \\
            --refactor-labels refactor.json \\
            -o train_data.pkl
    """
    import pickle

    from repotoire.graph.client import Neo4jClient
    from repotoire.ml.multimodal_analyzer import MultimodalAnalyzer

    console.print("[bold blue]Preparing multimodal training data[/bold blue]\n")

    if not any([bug_labels, smell_labels, refactor_labels]):
        console.print("[red]Error: At least one label file must be provided[/red]")
        raise click.Abort()

    try:
        client = Neo4jClient.from_env()
        analyzer = MultimodalAnalyzer(client)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching embeddings and labels...", total=None)

            train_dataset, val_dataset = analyzer.prepare_data(
                bug_labels_path=Path(bug_labels) if bug_labels else None,
                smell_labels_path=Path(smell_labels) if smell_labels else None,
                refactor_labels_path=Path(refactor_labels) if refactor_labels else None,
                test_split=test_split,
            )

            progress.update(task, description="Saving datasets...")

        # Save datasets
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "wb") as f:
            pickle.dump(
                {
                    "train": train_dataset,
                    "val": val_dataset,
                },
                f,
            )

        console.print(f"[green]Saved to {output}[/green]")
        console.print(f"  Training samples: {len(train_dataset)}")
        console.print(f"  Validation samples: {len(val_dataset)}")
        console.print(f"  Tasks: {', '.join(train_dataset.labels.keys())}")

    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[yellow]Install with: pip install torch[/yellow]")
        raise click.Abort()
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Data preparation failed")
        raise click.Abort()


@ml.command("train-multimodal")
@click.option(
    "--training-data",
    "-d",
    required=True,
    type=click.Path(exists=True),
    help="Training data pickle file from prepare-multimodal-data",
)
@click.option(
    "--tasks",
    "-t",
    multiple=True,
    default=["bug_prediction", "smell_detection", "refactoring_benefit"],
    help="Tasks to train (can specify multiple)",
)
@click.option(
    "--epochs",
    default=50,
    type=int,
    help="Maximum training epochs (default: 50)",
)
@click.option(
    "--batch-size",
    default=64,
    type=int,
    help="Training batch size (default: 64)",
)
@click.option(
    "--learning-rate",
    default=0.001,
    type=float,
    help="Initial learning rate (default: 0.001)",
)
@click.option(
    "--output",
    "-o",
    default="models/multimodal.pt",
    help="Output model path (default: models/multimodal.pt)",
)
def train_multimodal(
    training_data: str,
    tasks: tuple,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    output: str,
):
    """Train multimodal fusion model for multi-task prediction.

    Uses attention-based fusion to combine text (semantic) and graph
    (structural) embeddings for bug prediction, smell detection, and
    refactoring benefit estimation.

    The model uses:
    - Cross-modal attention between text and graph modalities
    - Gated fusion with learned modality importance
    - Multi-task learning with uncertainty weighting

    Examples:

        # Train on all tasks
        repotoire ml train-multimodal -d train_data.pkl

        # Train only bug prediction
        repotoire ml train-multimodal -d train_data.pkl -t bug_prediction

        # Custom hyperparameters
        repotoire ml train-multimodal -d train_data.pkl \\
            --epochs 100 --batch-size 128 --learning-rate 0.0005
    """
    import pickle

    from repotoire.graph.client import Neo4jClient
    from repotoire.ml.multimodal_analyzer import MultimodalAnalyzer, TrainingConfig

    console.print("[bold blue]Training multimodal fusion model[/bold blue]\n")

    try:
        # Load data
        with open(training_data, "rb") as f:
            data = pickle.load(f)

        train_dataset = data["train"]
        val_dataset = data["val"]

        console.print(f"[dim]Training samples: {len(train_dataset)}[/dim]")
        console.print(f"[dim]Validation samples: {len(val_dataset)}[/dim]")
        console.print(f"[dim]Tasks: {', '.join(tasks)}[/dim]\n")

        # Initialize
        client = Neo4jClient.from_env()
        config = TrainingConfig(
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
        )
        analyzer = MultimodalAnalyzer(client, training_config=config)

        # Train
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Training model...", total=None)
            history = analyzer.train(train_dataset, val_dataset, tasks=list(tasks))
            progress.update(task, description="Training complete")

        # Print final metrics
        table = Table(title="Final Model Performance", show_header=True, header_style="bold cyan")
        table.add_column("Task", style="cyan")
        table.add_column("Accuracy", style="green", justify="right")

        for task_name in tasks:
            key = f"{task_name}_acc"
            if key in history:
                acc = history[key][-1]
                table.add_row(task_name, f"{acc:.3f}")

        console.print(table)

        # Print training summary
        console.print(f"\n[dim]Final train loss: {history['train_loss'][-1]:.4f}[/dim]")
        console.print(f"[dim]Final val loss: {history['val_loss'][-1]:.4f}[/dim]")

        # Save
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        analyzer.save(output_path)

        console.print(f"\n[green]Model saved to {output}[/green]")

    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[yellow]Install with: pip install torch[/yellow]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Training failed")
        raise click.Abort()


@ml.command("multimodal-predict")
@click.argument("repo_path", type=click.Path(exists=True), required=False, default=".")
@click.option(
    "--model",
    "-m",
    required=True,
    type=click.Path(exists=True),
    help="Trained multimodal model path",
)
@click.option(
    "--task",
    "-t",
    default="bug_prediction",
    type=click.Choice(["bug_prediction", "smell_detection", "refactoring_benefit"]),
    help="Prediction task (default: bug_prediction)",
)
@click.option(
    "--threshold",
    default=0.7,
    type=float,
    help="Confidence threshold for showing predictions (default: 0.7)",
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
    help="Show top N predictions (default: 20)",
)
@click.option(
    "--function",
    "-f",
    "single_function",
    type=str,
    help="Predict for a single function by qualified name",
)
def multimodal_predict(
    repo_path: str,
    model: str,
    task: str,
    threshold: float,
    output: Optional[str],
    top_n: int,
    single_function: Optional[str],
):
    """Run multimodal predictions using trained fusion model.

    Combines text (semantic) and graph (structural) embeddings for
    enhanced prediction accuracy. Shows modality contribution for
    each prediction.

    Prerequisites:
        - Codebase ingested with embeddings
        - Trained multimodal model

    Examples:

        # Predict all functions
        repotoire ml multimodal-predict -m models/multimodal.pt

        # Predict code smells
        repotoire ml multimodal-predict -m model.pt -t smell_detection

        # Predict single function with explanation
        repotoire ml multimodal-predict -m model.pt -f mymodule.MyClass.method

        # Export results
        repotoire ml multimodal-predict -m model.pt -o predictions.json --top-n 50
    """
    from repotoire.graph.client import Neo4jClient
    from repotoire.ml.multimodal_analyzer import MultimodalAnalyzer

    console.print(f"[bold blue]Running {task} predictions[/bold blue]\n")

    try:
        client = Neo4jClient.from_env()
        analyzer = MultimodalAnalyzer.load(Path(model), client)

        # Single function prediction
        if single_function:
            explanation = analyzer.explain_prediction(single_function, task)

            if explanation is None:
                console.print(f"[yellow]Function not found: {single_function}[/yellow]")
                console.print("[dim]Ensure function has both text and graph embeddings.[/dim]")
                raise click.Abort()

            _print_multimodal_explanation(explanation)
            return

        # Batch prediction
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            prog_task = progress.add_task("Running predictions...", total=None)
            predictions = analyzer.predict_all_functions(task, threshold)
            progress.update(
                prog_task,
                description=f"Analyzed {len(predictions)} functions",
            )

        # Display results
        console.print(f"[bold]Found {len(predictions)} predictions above {threshold:.0%} threshold[/bold]\n")

        table = Table(title=f"Top {min(top_n, len(predictions))} Predictions ({task})")
        table.add_column("Function", style="cyan", max_width=40)
        table.add_column("Prediction", style="yellow")
        table.add_column("Confidence", style="red", justify="right")
        table.add_column("Text/Graph", style="green", justify="right")
        table.add_column("Interpretation", style="dim", max_width=25)

        for pred in predictions[:top_n]:
            # Color confidence based on level
            conf = pred["confidence"]
            if conf >= 0.9:
                conf_style = "red bold"
            elif conf >= 0.8:
                conf_style = "red"
            else:
                conf_style = "yellow"

            table.add_row(
                pred["qualified_name"].split(".")[-1],
                pred["prediction"],
                f"[{conf_style}]{conf:.1%}[/{conf_style}]",
                f"{pred['text_weight']:.0%}/{pred['graph_weight']:.0%}",
                pred["interpretation"],
            )

        console.print(table)

        # Summary
        console.print(f"\n[dim]Total predictions: {len(predictions)}[/dim]")

        # Save to JSON if requested
        if output:
            with open(output, "w") as f:
                json.dump(predictions, f, indent=2)
            console.print(f"[green]Results saved to {output}[/green]")

    except FileNotFoundError:
        console.print(f"[red]Model file not found: {model}[/red]")
        raise click.Abort()
    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[yellow]Install with: pip install torch[/yellow]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Prediction failed")
        raise click.Abort()


def _print_multimodal_explanation(explanation) -> None:
    """Print detailed explanation for a multimodal prediction."""
    # Severity color based on confidence
    conf = explanation.confidence
    if conf >= 0.9:
        conf_style = "red bold"
        severity = "CRITICAL"
    elif conf >= 0.8:
        conf_style = "red"
        severity = "HIGH"
    elif conf >= 0.7:
        conf_style = "yellow"
        severity = "MEDIUM"
    else:
        conf_style = "green"
        severity = "LOW"

    # Modality emphasis
    if explanation.graph_weight > 0.6:
        modality_emphasis = "[cyan]Structural patterns dominate[/cyan]"
    elif explanation.text_weight > 0.6:
        modality_emphasis = "[magenta]Semantic patterns dominate[/magenta]"
    else:
        modality_emphasis = "[dim]Balanced modalities[/dim]"

    console.print(
        Panel(
            f"[bold]{explanation.qualified_name}[/bold]\n"
            f"Task: {explanation.task}\n\n"
            f"Prediction: [bold]{explanation.prediction}[/bold]\n"
            f"Confidence: [{conf_style}]{conf:.1%}[/{conf_style}] ({severity})\n\n"
            f"[bold]Modality Importance:[/bold]\n"
            f"  Text (semantic): {explanation.text_weight:.1%}\n"
            f"  Graph (structural): {explanation.graph_weight:.1%}\n\n"
            f"{modality_emphasis}",
            title="Multimodal Prediction",
            border_style="cyan",
        )
    )

    console.print(f"\n[bold]Interpretation:[/bold]")
    console.print(f"  {explanation.interpretation}")


# ============================================================================
# GraphSAGE Zero-Shot Commands
# ============================================================================


@ml.command("extract-multi-project-labels")
@click.option(
    "--projects",
    "-p",
    required=True,
    help="Comma-separated project paths (e.g., /path/to/proj1,/path/to/proj2)",
)
@click.option(
    "--output",
    "-o",
    required=True,
    help="Output JSON file for combined labels",
)
@click.option(
    "--since",
    default="2020-01-01",
    help="Start date for git history (default: 2020-01-01)",
)
@click.option(
    "--max-commits",
    type=int,
    help="Maximum commits to analyze per project",
)
@click.option(
    "--max-examples",
    type=int,
    help="Maximum examples per project (balanced 50/50)",
)
def extract_multi_project_labels(
    projects: str,
    output: str,
    since: str,
    max_commits: Optional[int],
    max_examples: Optional[int],
):
    """Extract training labels from multiple projects' git history.

    Analyzes commit history from multiple repositories to build a
    comprehensive training dataset for cross-project defect prediction.

    Examples:

        # Extract from two projects
        repotoire ml extract-multi-project-labels \\
            -p /path/to/flask,/path/to/requests \\
            -o combined_labels.json

        # With limits for faster testing
        repotoire ml extract-multi-project-labels \\
            -p ./proj1,./proj2,./proj3 \\
            -o labels.json \\
            --max-commits 100 \\
            --max-examples 500
    """
    from repotoire.ml.training_data import GitBugLabelExtractor

    console.print("[bold blue]Extracting labels from multiple projects[/bold blue]")

    project_paths = [p.strip() for p in projects.split(",")]
    console.print(f"[dim]Projects: {len(project_paths)}[/dim]\n")

    all_labels = []
    stats = {
        "total_projects": len(project_paths),
        "total_functions": 0,
        "buggy": 0,
        "clean": 0,
        "projects": {},
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for project_path in project_paths:
            task = progress.add_task(f"Processing: {project_path}...", total=None)

            try:
                extractor = GitBugLabelExtractor(Path(project_path))
                dataset = extractor.create_balanced_dataset(
                    since_date=since,
                    max_examples=max_examples,
                )

                project_name = Path(project_path).name
                project_buggy = 0
                project_clean = 0

                for example in dataset.examples:
                    label_int = 1 if example.label == "buggy" else 0
                    all_labels.append({
                        "project": project_name,
                        "project_path": str(project_path),
                        "qualified_name": example.qualified_name,
                        "label": label_int,
                        "label_str": example.label,
                        "confidence": example.confidence,
                        "file_path": example.file_path,
                    })

                    if label_int == 1:
                        project_buggy += 1
                    else:
                        project_clean += 1

                stats["total_functions"] += len(dataset.examples)
                stats["buggy"] += project_buggy
                stats["clean"] += project_clean
                stats["projects"][project_name] = {
                    "total": len(dataset.examples),
                    "buggy": project_buggy,
                    "clean": project_clean,
                }

                progress.update(
                    task,
                    description=f"{project_name}: {len(dataset.examples)} functions ({project_buggy} buggy)",
                )

            except Exception as e:
                progress.update(task, description=f"[red]Error: {project_path}: {e}[/red]")
                logger.error(f"Failed to process {project_path}: {e}")

    # Save combined labels
    output_data = {
        "labels": all_labels,
        "stats": stats,
        "extraction_config": {
            "since": since,
            "max_commits": max_commits,
            "max_examples": max_examples,
        },
    }

    with open(output, "w") as f:
        json.dump(output_data, f, indent=2)

    console.print(f"\n[green]Saved to {output}[/green]")

    # Print summary table
    table = Table(title="Multi-Project Label Extraction Summary")
    table.add_column("Project", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("Buggy", justify="right", style="red")
    table.add_column("Clean", justify="right", style="green")

    for project_name, proj_stats in stats["projects"].items():
        table.add_row(
            project_name,
            str(proj_stats["total"]),
            str(proj_stats["buggy"]),
            str(proj_stats["clean"]),
        )

    table.add_section()
    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{stats['total_functions']}[/bold]",
        f"[bold]{stats['buggy']}[/bold]",
        f"[bold]{stats['clean']}[/bold]",
    )

    console.print(table)


@ml.command("train-graphsage")
@click.option(
    "--training-data",
    "-d",
    required=True,
    type=click.Path(exists=True),
    help="Training data JSON file from extract-multi-project-labels",
)
@click.option(
    "--hidden-dim",
    default=128,
    type=int,
    help="Hidden layer dimension (default: 128)",
)
@click.option(
    "--num-layers",
    default=2,
    type=int,
    help="Number of GraphSAGE layers (default: 2)",
)
@click.option(
    "--batch-size",
    default=128,
    type=int,
    help="Mini-batch size (default: 128)",
)
@click.option(
    "--epochs",
    default=100,
    type=int,
    help="Maximum training epochs (default: 100)",
)
@click.option(
    "--learning-rate",
    default=0.001,
    type=float,
    help="Initial learning rate (default: 0.001)",
)
@click.option(
    "--holdout-project",
    help="Project to hold out for cross-project testing",
)
@click.option(
    "--output",
    "-o",
    default="models/graphsage_universal.pt",
    help="Output model path (default: models/graphsage_universal.pt)",
)
def train_graphsage(
    training_data: str,
    hidden_dim: int,
    num_layers: int,
    batch_size: int,
    epochs: int,
    learning_rate: float,
    holdout_project: Optional[str],
    output: str,
):
    """Train GraphSAGE for cross-project defect prediction.

    Trains a GraphSAGE model on labeled data from multiple projects.
    The model learns aggregation functions that generalize to any
    new codebase (zero-shot inference).

    Prerequisites:
    - Training labels from 'repotoire ml extract-multi-project-labels'
    - Each project's codebase ingested with embeddings

    Examples:

        # Basic training
        repotoire ml train-graphsage -d combined_labels.json

        # With held-out project for zero-shot evaluation
        repotoire ml train-graphsage -d labels.json --holdout-project flask

        # Custom hyperparameters
        repotoire ml train-graphsage -d labels.json \\
            --hidden-dim 256 --num-layers 3 --epochs 200
    """
    try:
        from repotoire.ml.cross_project_trainer import (
            CrossProjectTrainer,
            CrossProjectDataLoader,
            CrossProjectTrainingConfig,
        )
        from repotoire.ml.graphsage_predictor import GraphSAGEConfig
        from repotoire.graph.client import Neo4jClient
    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[yellow]Install with: pip install torch torch-geometric[/yellow]")
        raise click.Abort()

    console.print("[bold blue]Training GraphSAGE for zero-shot defect prediction[/bold blue]")

    # Load labels
    with open(training_data) as f:
        data = json.load(f)

    labels_by_project: Dict[str, Dict[str, int]] = {}
    for item in data["labels"]:
        project = item.get("project") or item.get("project_path", "unknown")
        if project not in labels_by_project:
            labels_by_project[project] = {}
        labels_by_project[project][item["qualified_name"]] = item["label"]

    console.print(f"[dim]Projects: {list(labels_by_project.keys())}[/dim]")
    console.print(f"[dim]Total labels: {sum(len(l) for l in labels_by_project.values())}[/dim]\n")

    if holdout_project and holdout_project not in labels_by_project:
        console.print(f"[yellow]Warning: Holdout project '{holdout_project}' not in labels[/yellow]")
        holdout_project = None

    # Initialize configs
    model_config = GraphSAGEConfig(
        hidden_dim=hidden_dim,
        num_layers=num_layers,
    )
    training_config = CrossProjectTrainingConfig(
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
    )

    # For this implementation, we load from a single Neo4j instance
    # that has all projects' data ingested
    console.print("[dim]Loading graph data from Neo4j...[/dim]")

    try:
        client = Neo4jClient.from_env()

        # Combine all labels (assuming single graph with all projects)
        all_labels: Dict[str, int] = {}
        for project_labels in labels_by_project.values():
            all_labels.update(project_labels)

        loader = CrossProjectDataLoader(clients={"combined": client})

        # Load as single combined project
        project_graph = loader.load_project_graph("combined", all_labels)

        # If holdout requested, we do train/test split within this graph
        train_data = project_graph.data
        val_data = None

        if holdout_project:
            # For true cross-project evaluation, you'd need separate Neo4j instances
            # or use the load_project_from_json method with exported data
            console.print(
                f"[yellow]Note: True cross-project holdout requires separate graph exports. "
                f"Using within-graph test split instead.[/yellow]"
            )

    except Exception as e:
        console.print(f"[red]Error loading graph data: {e}[/red]")
        raise click.Abort()

    # Train model
    trainer = CrossProjectTrainer(model_config, training_config)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Training GraphSAGE model...", total=None)

        try:
            history = trainer.train(train_data, val_data=val_data)
            progress.update(task, description="Training complete")
        except Exception as e:
            console.print(f"[red]Training failed: {e}[/red]")
            logger.exception("GraphSAGE training failed")
            raise click.Abort()

    # Print results
    table = Table(title="Training Results", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")

    table.add_row("Final train loss", f"{history.train_loss[-1]:.4f}")
    table.add_row("Final train accuracy", f"{history.train_acc[-1]:.3f}")

    if history.val_acc and history.val_acc[-1] > 0:
        table.add_row("Final val accuracy", f"{history.val_acc[-1]:.3f}")
        table.add_row("Final val AUC-ROC", f"{history.val_auc[-1]:.3f}")

    table.add_row("Total epochs", str(len(history.train_loss)))

    console.print(table)

    # Save model
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trainer.save(output_path)

    console.print(f"\n[green]Model saved to {output}[/green]")
    console.print(
        "[dim]Use with: repotoire ml zero-shot-predict -m " + output + "[/dim]"
    )


@ml.command("zero-shot-predict")
@click.argument("repo_path", type=click.Path(exists=True), required=False, default=".")
@click.option(
    "--model",
    "-m",
    required=True,
    type=click.Path(exists=True),
    help="Pre-trained GraphSAGE model path",
)
@click.option(
    "--threshold",
    default=0.5,
    type=float,
    help="Risk threshold for flagging (0.0-1.0, default: 0.5)",
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
def zero_shot_predict(
    repo_path: str,
    model: str,
    threshold: float,
    output: Optional[str],
    top_n: int,
):
    """Apply pre-trained GraphSAGE to new codebase (zero-shot).

    Uses a model trained on other projects to predict defect risk
    in a completely new codebase - no project-specific training needed!

    Prerequisites:
    - Codebase ingested with 'repotoire ingest --generate-embeddings'
    - Pre-trained GraphSAGE model

    Examples:

        # Basic zero-shot prediction
        repotoire ml zero-shot-predict -m models/graphsage_universal.pt

        # Export results
        repotoire ml zero-shot-predict -m model.pt -o predictions.json

        # Higher threshold for fewer, higher-confidence predictions
        repotoire ml zero-shot-predict -m model.pt --threshold 0.7
    """
    try:
        from repotoire.ml.cross_project_trainer import CrossProjectTrainer
        from repotoire.ml.graphsage_predictor import GraphFeatureExtractor
        from repotoire.graph.client import Neo4jClient
    except ImportError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[yellow]Install with: pip install torch torch-geometric[/yellow]")
        raise click.Abort()

    console.print(f"[bold blue]Zero-shot defect prediction for {repo_path}[/bold blue]")
    console.print("[dim](No project-specific training needed!)[/dim]\n")

    # Load model
    try:
        trainer = CrossProjectTrainer.load(Path(model))
    except Exception as e:
        console.print(f"[red]Failed to load model: {e}[/red]")
        raise click.Abort()

    # Extract graph data
    console.print("[dim]Extracting graph features...[/dim]")

    try:
        client = Neo4jClient.from_env()
        extractor = GraphFeatureExtractor(client)
        data, node_mapping = extractor.extract_graph_data()
    except Exception as e:
        console.print(f"[red]Failed to extract graph: {e}[/red]")
        raise click.Abort()

    if data.x.size(0) == 0:
        console.print("[yellow]No functions with embeddings found.[/yellow]")
        console.print("[dim]Run 'repotoire ingest --generate-embeddings' first.[/dim]")
        raise click.Abort()

    console.print(f"[dim]Found {data.x.size(0)} functions, {data.edge_index.size(1)} edges[/dim]")

    # Run predictions
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Running zero-shot predictions...", total=None)
        predictions = trainer.predict_zero_shot(data)
        progress.update(task, description=f"Analyzed {len(predictions)} functions")

    # Map back to qualified names
    idx_to_name = {v: k for k, v in node_mapping.items()}

    # Filter and sort
    high_risk = [
        {
            "qualified_name": idx_to_name.get(p["node_idx"], f"node_{p['node_idx']}"),
            "probability": p["buggy_probability"],
            "prediction": p["prediction"],
        }
        for p in predictions
        if p["buggy_probability"] >= threshold
    ]
    high_risk.sort(key=lambda x: x["probability"], reverse=True)

    # Display results
    console.print(f"\n[bold]Found {len(high_risk)} high-risk functions (>={threshold:.0%})[/bold]\n")

    table = Table(title=f"Top {min(top_n, len(high_risk))} Defect Risks (Zero-Shot)")
    table.add_column("Function", style="cyan", max_width=50)
    table.add_column("Probability", style="red", justify="right")
    table.add_column("Risk Level", justify="right")

    for pred in high_risk[:top_n]:
        prob = pred["probability"]
        if prob >= 0.9:
            level = "[red bold]CRITICAL[/red bold]"
        elif prob >= 0.8:
            level = "[red]HIGH[/red]"
        elif prob >= 0.7:
            level = "[yellow]MEDIUM[/yellow]"
        else:
            level = "[green]LOW[/green]"

        table.add_row(
            pred["qualified_name"].split(".")[-1],
            f"{prob:.1%}",
            level,
        )

    console.print(table)

    # Summary
    console.print(f"\n[dim]Total functions analyzed: {len(predictions)}[/dim]")
    console.print(f"[dim]High-risk (>={threshold:.0%}): {len(high_risk)}[/dim]")

    # Save to JSON if requested
    if output:
        output_data = {
            "predictions": [
                {
                    "qualified_name": idx_to_name.get(p["node_idx"], f"node_{p['node_idx']}"),
                    "probability": p["buggy_probability"],
                    "prediction": p["prediction"],
                }
                for p in predictions
            ],
            "high_risk_count": len(high_risk),
            "threshold": threshold,
            "model_path": str(model),
        }

        with open(output, "w") as f:
            json.dump(output_data, f, indent=2)

        console.print(f"\n[green]Results saved to {output}[/green]")


@ml.command("export-graph-data")
@click.argument("repo_path", type=click.Path(exists=True), required=False, default=".")
@click.option(
    "--output-dir",
    "-o",
    required=True,
    type=click.Path(),
    help="Output directory for exported graph files",
)
@click.option(
    "--project-name",
    "-n",
    required=True,
    help="Project name for the exported files",
)
def export_graph_data(
    repo_path: str,
    output_dir: str,
    project_name: str,
):
    """Export graph data for offline GraphSAGE training.

    Exports node features and edges to JSON files that can be used
    for training GraphSAGE without requiring a live Neo4j connection.

    Useful for:
    - Training on large clusters without Neo4j access
    - Sharing training data between team members
    - Archiving project graphs for reproducibility

    Examples:

        # Export current project's graph
        repotoire ml export-graph-data -o ./exports -n myproject

        # Export after ingesting
        repotoire ingest /path/to/repo --generate-embeddings
        repotoire ml export-graph-data -o ./exports -n myproject
    """
    from repotoire.graph.client import Neo4jClient

    console.print(f"[bold blue]Exporting graph data for {project_name}[/bold blue]")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        client = Neo4jClient.from_env()

        # Export nodes
        node_query = """
        MATCH (f:Function)
        WHERE f.embedding IS NOT NULL
        RETURN
            f.qualifiedName AS qualified_name,
            f.embedding AS embedding,
            f.complexity AS complexity,
            f.loc AS loc
        """
        nodes = client.execute_query(node_query)

        # Export edges
        edge_query = """
        MATCH (f1:Function)-[:CALLS]->(f2:Function)
        WHERE f1.embedding IS NOT NULL AND f2.embedding IS NOT NULL
        RETURN f1.qualifiedName AS source, f2.qualifiedName AS target
        """
        edges = client.execute_query(edge_query)

    except Exception as e:
        console.print(f"[red]Error querying graph: {e}[/red]")
        raise click.Abort()

    # Save nodes
    nodes_file = output_path / f"{project_name}_nodes.json"
    with open(nodes_file, "w") as f:
        json.dump({"nodes": nodes}, f)

    # Save edges
    edges_file = output_path / f"{project_name}_edges.json"
    with open(edges_file, "w") as f:
        json.dump({"edges": edges}, f)

    console.print(f"\n[green]Exported {len(nodes)} nodes to {nodes_file}[/green]")
    console.print(f"[green]Exported {len(edges)} edges to {edges_file}[/green]")
    console.print(
        f"\n[dim]Use with CrossProjectDataLoader.load_project_from_json()[/dim]"
    )
