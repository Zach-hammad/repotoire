"""CLI commands for git history RAG queries.

Provides natural language queries over git commit history using RAG.
This is 99% cheaper than the old Graphiti approach:
- Old: $10-20 to ingest + $0.01/query (LLM per commit)
- New: FREE to ingest + $0.001/query (local embeddings + Claude Haiku)

Commands:
- repotoire historical ask "question" - Ask about git history
- repotoire historical search "query" - Semantic search (no LLM)
- repotoire historical ingest /path - Ingest commits
- repotoire historical status - Check embeddings coverage
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from repotoire.logging_config import get_logger

console = Console()
logger = get_logger(__name__)


@click.group()
def historical():
    """Git history RAG commands (natural language queries).

    Query git commit history using natural language. Uses semantic vector
    search + Claude Haiku to answer questions about code evolution.

    \b
    Examples:
      $ repotoire historical ask "When did we add OAuth?"
      $ repotoire historical search "authentication changes"
      $ repotoire historical ingest ./my-repo --max-commits 100
      $ repotoire historical status

    \b
    Cost: ~$0.001/query (99% cheaper than Graphiti)
    """
    pass


@historical.command()
@click.argument("query")
@click.option(
    "--repo-path",
    "-p",
    type=click.Path(exists=True),
    default=".",
    help="Path to git repository (default: current directory)",
)
@click.option(
    "--top-k",
    type=int,
    default=10,
    help="Number of commits to retrieve for context (default: 10)",
)
@click.option(
    "--author",
    "-a",
    default=None,
    help="Filter by author email",
)
@click.option(
    "--since",
    type=click.DateTime(),
    default=None,
    help="Filter commits after this date (YYYY-MM-DD)",
)
@click.option(
    "--until",
    type=click.DateTime(),
    default=None,
    help="Filter commits before this date (YYYY-MM-DD)",
)
@click.option(
    "--embedding-backend",
    type=click.Choice(["local", "openai", "deepinfra", "voyage"], case_sensitive=False),
    default="local",
    help="Embedding backend (default: local - FREE)",
)
def ask(
    query: str,
    repo_path: str,
    top_k: int,
    author: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
    embedding_backend: str,
) -> None:
    """Ask a question about git history using RAG.

    Uses semantic vector search to find relevant commits, then generates
    a natural language answer using Claude Haiku (~$0.001/query).

    \b
    Examples:
      repotoire historical ask "When did we add OAuth authentication?"
      repotoire historical ask "What changes did Alice make to the parser?"
      repotoire historical ask "Show refactorings of UserManager" --top-k 20
      repotoire historical ask "What happened last month?" --since 2024-01-01

    Requires commits to be ingested first:
      repotoire historical ingest /path/to/repo
    """
    import os

    # Get repo_id from path (hash of absolute path as fallback)
    import hashlib

    repo_abs_path = str(Path(repo_path).resolve())
    repo_id = hashlib.sha256(repo_abs_path.encode()).hexdigest()[:36]

    try:
        from repotoire.ai.embeddings import CodeEmbedder
        from repotoire.graph.factory import create_client
        from repotoire.historical.git_rag import GitHistoryRAG
    except ImportError as e:
        console.print(f"[red]Error:[/red] Missing dependencies: {e}")
        console.print("[dim]Install with: pip install repotoire[rag][/dim]")
        raise click.Abort()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Searching git history...", total=None)

        try:
            # Initialize RAG
            graph_client = create_client(show_cloud_indicator=False)

            # Use environment variable or default to local
            backend = os.environ.get("REPOTOIRE_EMBEDDING_BACKEND", embedding_backend)
            embedder = CodeEmbedder(backend=backend)
            rag = GitHistoryRAG(client=graph_client, embedder=embedder)

            # Run query
            async def run_query():
                return await rag.ask(
                    query=query,
                    repo_id=repo_id,
                    top_k=top_k,
                    author=author,
                    since=since,
                    until=until,
                )

            answer = asyncio.run(run_query())

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            logger.exception("Historical ask failed")
            raise click.Abort()

    # Display answer
    console.print()
    console.print(Panel(answer.answer, title="[bold]Answer[/bold]", border_style="green"))
    console.print()

    # Display confidence
    confidence_color = "green" if answer.confidence > 0.7 else "yellow" if answer.confidence > 0.4 else "red"
    console.print(f"[dim]Confidence:[/dim] [{confidence_color}]{answer.confidence:.0%}[/{confidence_color}]")
    console.print(f"[dim]Execution time:[/dim] {answer.execution_time_ms:.0f}ms")

    # Display relevant commits
    if answer.commits:
        console.print()
        table = Table(title="Relevant Commits", box=None)
        table.add_column("SHA", style="cyan")
        table.add_column("Date", style="dim")
        table.add_column("Author", style="blue")
        table.add_column("Message")
        table.add_column("Score", justify="right")

        for result in answer.commits[:7]:
            c = result.commit
            date_str = c.committed_at.strftime("%Y-%m-%d") if c.committed_at else "-"
            table.add_row(
                c.short_sha,
                date_str,
                c.author_name[:20],
                c.message_subject[:50] + "..." if len(c.message_subject) > 50 else c.message_subject,
                f"{result.score:.2f}",
            )

        console.print(table)

    # Display follow-up questions
    if answer.follow_up_questions:
        console.print()
        console.print("[dim]Suggested follow-up questions:[/dim]")
        for q in answer.follow_up_questions:
            console.print(f"  • {q}")


@historical.command()
@click.argument("query")
@click.option(
    "--repo-path",
    "-p",
    type=click.Path(exists=True),
    default=".",
    help="Path to git repository (default: current directory)",
)
@click.option(
    "--top-k",
    type=int,
    default=20,
    help="Number of commits to return (default: 20)",
)
@click.option(
    "--author",
    "-a",
    default=None,
    help="Filter by author email",
)
@click.option(
    "--since",
    type=click.DateTime(),
    default=None,
    help="Filter commits after this date",
)
@click.option(
    "--until",
    type=click.DateTime(),
    default=None,
    help="Filter commits before this date",
)
@click.option(
    "--embedding-backend",
    type=click.Choice(["local", "openai", "deepinfra", "voyage"], case_sensitive=False),
    default="local",
    help="Embedding backend (default: local - FREE)",
)
def search(
    query: str,
    repo_path: str,
    top_k: int,
    author: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
    embedding_backend: str,
) -> None:
    """Semantic search over git history (no LLM, faster).

    Uses vector similarity search to find relevant commits without
    generating a natural language answer. Useful for browsing/exploring.

    \b
    Examples:
      repotoire historical search "OAuth authentication"
      repotoire historical search "bug fix" --author alice@example.com
      repotoire historical search "refactoring" --since 2024-01-01
    """
    import os
    import hashlib

    repo_abs_path = str(Path(repo_path).resolve())
    repo_id = hashlib.sha256(repo_abs_path.encode()).hexdigest()[:36]

    try:
        from repotoire.ai.embeddings import CodeEmbedder
        from repotoire.graph.factory import create_client
        from repotoire.historical.git_rag import GitHistoryRAG
    except ImportError as e:
        console.print(f"[red]Error:[/red] Missing dependencies: {e}")
        raise click.Abort()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Searching commits...", total=None)

        try:
            graph_client = create_client(show_cloud_indicator=False)
            backend = os.environ.get("REPOTOIRE_EMBEDDING_BACKEND", embedding_backend)
            embedder = CodeEmbedder(backend=backend)
            rag = GitHistoryRAG(client=graph_client, embedder=embedder)

            async def run_search():
                return await rag.search(
                    query=query,
                    repo_id=repo_id,
                    top_k=top_k,
                    author=author,
                    since=since,
                    until=until,
                )

            results = asyncio.run(run_search())

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise click.Abort()

    # Display results
    console.print()
    console.print(f"[bold]Found {len(results)} commits matching:[/bold] {query}")
    console.print()

    if results:
        table = Table(box=None)
        table.add_column("SHA", style="cyan")
        table.add_column("Date", style="dim")
        table.add_column("Author", style="blue")
        table.add_column("Message")
        table.add_column("+/-", justify="right")
        table.add_column("Score", justify="right", style="green")

        for result in results:
            c = result.commit
            date_str = c.committed_at.strftime("%Y-%m-%d") if c.committed_at else "-"
            changes = f"+{c.insertions}/-{c.deletions}"
            message = c.message_subject[:60] + "..." if len(c.message_subject) > 60 else c.message_subject

            table.add_row(
                c.short_sha,
                date_str,
                c.author_name[:15],
                message,
                changes,
                f"{result.score:.2f}",
            )

        console.print(table)
    else:
        console.print("[yellow]No commits found.[/yellow]")
        console.print("[dim]Try running 'repotoire historical ingest' first.[/dim]")


@historical.command("ingest")
@click.argument("repo_path", type=click.Path(exists=True), default=".")
@click.option(
    "--max-commits",
    type=int,
    default=100,
    help="Maximum commits to ingest (default: 100)",
)
@click.option(
    "--embedding-backend",
    type=click.Choice(["local", "openai", "deepinfra", "voyage"], case_sensitive=False),
    default="local",
    help="Embedding backend (default: local - FREE)",
)
@click.option(
    "--batch-size",
    type=int,
    default=50,
    help="Batch size for embedding generation (default: 50)",
)
def ingest_git(
    repo_path: str,
    max_commits: int,
    embedding_backend: str,
    batch_size: int,
) -> None:
    """Ingest git history into the graph with embeddings.

    Extracts commits from the git repository, generates embeddings using
    the local backend (FREE), and stores them in FalkorDB for RAG queries.

    \b
    Examples:
      repotoire historical ingest ./my-repo
      repotoire historical ingest . --max-commits 500
      repotoire historical ingest ~/code/project --embedding-backend deepinfra

    \b
    Cost comparison:
      - Local backend (default): FREE
      - DeepInfra: ~$0.01 per 1000 commits
      - OpenAI: ~$0.02 per 1000 commits
    """
    import os
    import hashlib

    repo_abs_path = str(Path(repo_path).resolve())
    repo_id = hashlib.sha256(repo_abs_path.encode()).hexdigest()[:36]

    try:
        from repotoire.ai.embeddings import CodeEmbedder
        from repotoire.graph.factory import create_client
        from repotoire.historical.git_rag import GitHistoryRAG
        from repotoire.integrations.git import GitRepository
    except ImportError as e:
        console.print(f"[red]Error:[/red] Missing dependencies: {e}")
        raise click.Abort()

    console.print(f"[bold]Ingesting git history from:[/bold] {repo_path}")
    console.print(f"[dim]Max commits: {max_commits}, Backend: {embedding_backend}[/dim]")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Get commits
        task1 = progress.add_task("Reading git history...", total=None)

        try:
            git_repo = GitRepository(repo_path)
            commits = git_repo.get_commit_history(max_commits=max_commits)
            progress.remove_task(task1)
        except Exception as e:
            console.print(f"[red]Error reading git history:[/red] {e}")
            raise click.Abort()

        if not commits:
            console.print("[yellow]No commits found in repository.[/yellow]")
            raise click.Abort()

        console.print(f"[green]✓[/green] Found {len(commits)} commits")

        # Initialize RAG
        task2 = progress.add_task("Initializing embedder...", total=None)

        try:
            graph_client = create_client(show_cloud_indicator=False)
            backend = os.environ.get("REPOTOIRE_EMBEDDING_BACKEND", embedding_backend)
            embedder = CodeEmbedder(backend=backend)
            rag = GitHistoryRAG(client=graph_client, embedder=embedder)
            progress.remove_task(task2)
        except Exception as e:
            console.print(f"[red]Error initializing:[/red] {e}")
            raise click.Abort()

        console.print(f"[green]✓[/green] Initialized {backend} embedder")

        # Ingest commits
        task3 = progress.add_task("Generating embeddings and storing...", total=None)

        try:
            async def run_ingest():
                return await rag.ingest_commits(
                    commits=commits,
                    repo_id=repo_id,
                    batch_size=batch_size,
                )

            stats = asyncio.run(run_ingest())
            progress.remove_task(task3)

        except Exception as e:
            console.print(f"[red]Error ingesting:[/red] {e}")
            logger.exception("Ingest failed")
            raise click.Abort()

    # Display results
    console.print()
    console.print("[bold green]✓ Ingestion complete![/bold green]")
    console.print()

    table = Table(box=None, show_header=False)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Commits processed", str(stats.get("commits_processed", 0)))
    table.add_row("Embeddings generated", str(stats.get("embeddings_generated", 0)))
    table.add_row("Relationships created", str(stats.get("relationships_created", 0)))
    table.add_row("Errors", str(stats.get("errors", 0)))
    table.add_row("Time", f"{stats.get('elapsed_seconds', 0):.1f}s")
    table.add_row("Rate", f"{stats.get('commits_per_second', 0):.1f} commits/sec")

    console.print(table)
    console.print()
    console.print("[dim]You can now query with:[/dim]")
    console.print(f"  repotoire historical ask \"When did we add feature X?\" -p {repo_path}")


@historical.command("status")
@click.option(
    "--repo-path",
    "-p",
    type=click.Path(exists=True),
    default=".",
    help="Path to git repository (default: current directory)",
)
def status(repo_path: str) -> None:
    """Check git history RAG status for a repository.

    Shows the number of commits ingested, embedding coverage,
    and whether RAG queries are available.
    """
    import hashlib

    repo_abs_path = str(Path(repo_path).resolve())
    repo_id = hashlib.sha256(repo_abs_path.encode()).hexdigest()[:36]

    try:
        from repotoire.ai.embeddings import CodeEmbedder
        from repotoire.graph.factory import create_client
        from repotoire.historical.git_rag import GitHistoryRAG
    except ImportError as e:
        console.print(f"[red]Error:[/red] Missing dependencies: {e}")
        raise click.Abort()

    try:
        graph_client = create_client(show_cloud_indicator=False)
        embedder = CodeEmbedder(backend="local")
        rag = GitHistoryRAG(client=graph_client, embedder=embedder)

        status_info = rag.get_embeddings_status(repo_id)
        commit_count = rag.get_commit_count(repo_id)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise click.Abort()

    console.print()
    console.print(f"[bold]Git History RAG Status[/bold]")
    console.print(f"[dim]Repository: {repo_path}[/dim]")
    console.print()

    table = Table(box=None, show_header=False)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Total commits", str(status_info.get("total_commits", 0)))
    table.add_row("With embeddings", str(status_info.get("commits_with_embeddings", 0)))

    coverage = status_info.get("coverage", 0.0)
    coverage_color = "green" if coverage > 0.9 else "yellow" if coverage > 0.5 else "red"
    table.add_row("Coverage", f"[{coverage_color}]{coverage:.0%}[/{coverage_color}]")

    rag_ready = status_info.get("total_commits", 0) > 0
    rag_status = "[green]Ready[/green]" if rag_ready else "[red]Not ready[/red]"
    table.add_row("RAG status", rag_status)

    console.print(table)

    if not rag_ready:
        console.print()
        console.print("[dim]To enable RAG queries, run:[/dim]")
        console.print(f"  repotoire historical ingest {repo_path}")
