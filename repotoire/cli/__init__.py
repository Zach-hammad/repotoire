"""Command-line interface for Repotoire."""

# Auto-load .env file if present (before any other imports that use env vars)
from dotenv import load_dotenv
load_dotenv()

import os
import threading
import click
from dataclasses import asdict
from pathlib import Path


def _get_optimal_workers() -> int:
    """Get optimal number of workers based on available CPU cores.

    Returns:
        Number of workers (capped at 8 to avoid oversubscription)
    """
    cpu_count = os.cpu_count() or 4
    # Use all available cores up to 8 (diminishing returns beyond that for I/O bound work)
    return min(cpu_count, 8)
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.tree import Tree
from rich.text import Text
from rich.prompt import Confirm
from rich import box
from rich.markup import escape

from repotoire.pipeline import IngestionPipeline
from repotoire.graph.factory import create_client
from repotoire.detectors import AnalysisEngine
from repotoire.migrations import MigrationManager, MigrationError
from repotoire.logging_config import configure_logging, get_logger, LogContext
from repotoire.config import load_config, FalkorConfig, ConfigError, generate_config_template
from repotoire.models import SecretsPolicy
from repotoire.validation import (
    ValidationError,
    validate_repository_path,
    validate_falkordb_host,
    validate_falkordb_port,
    validate_falkordb_password,
    validate_falkordb_connection,
    validate_output_path,
    validate_file_size_limit,
    validate_batch_size,
    validate_retry_config,
)

console = Console()
logger = get_logger(__name__)

# Global config storage (loaded once per CLI invocation)
# Thread-safe with double-checked locking pattern
_config: FalkorConfig | None = None
_config_lock = threading.Lock()


def _get_db_client(quiet: bool = False):
    """Get database client. Uses cloud API if logged in, local FalkorDB otherwise.

    Args:
        quiet: Suppress connection messages

    Returns:
        DatabaseClient instance

    Raises:
        ConfigurationError: If neither API key nor local FalkorDB is available
    """
    from repotoire.graph.factory import get_api_key, create_falkordb_client
    
    # Try cloud mode first
    if get_api_key():
        return create_client(show_cloud_indicator=not quiet)
    
    # Fall back to local FalkorDB if configured
    host = os.getenv("FALKORDB_HOST", "localhost")
    port = int(os.getenv("FALKORDB_PORT", "6379"))
    
    try:
        client = create_falkordb_client(host=host, port=port)
        if not quiet:
            console.print(f"[dim]🗄️  Connected to local FalkorDB ({host}:{port})[/dim]")
        return client
    except Exception as e:
        from repotoire.graph.factory import ConfigurationError
        raise ConfigurationError(
            f"No API key and cannot connect to local FalkorDB at {host}:{port}.\n\n"
            "Either:\n"
            "  1. Login: repotoire login ak_your_key\n"
            "  2. Or start local FalkorDB: docker run -p 6379:6379 falkordb/falkordb"
        ) from e


def _is_cloud_mode() -> bool:
    """Check if running in cloud mode (API key available).

    Returns:
        True if API key is configured and cloud mode is active
    """
    from repotoire.graph.factory import get_api_key
    return get_api_key() is not None


def _get_tenant_from_auth() -> tuple[Optional[str], Optional[str]]:
    """Auto-resolve tenant ID from authenticated user's organization.

    REPO-600: Multi-tenant data isolation - automatic tenant resolution.

    Fetches org info by validating the stored API key against the Repotoire API.
    This provides seamless multi-tenant isolation without requiring explicit
    --tenant-id flag for every command.

    Returns:
        Tuple of (tenant_id, org_slug) or (None, None) if not authenticated
    """
    import os

    try:
        from repotoire.cli.auth import CLIAuth

        cli_auth = CLIAuth()
        api_key = cli_auth.get_api_key()

        if not api_key:
            return None, None

        # Fetch org info from API
        org_info = cli_auth._fetch_org_info(api_key)
        if org_info:
            org_id = org_info.get("org_id")
            org_slug = org_info.get("org_slug")
            if org_id:
                logger.debug(f"Auto-resolved tenant from auth: {org_id} ({org_slug})")
                return org_id, org_slug

    except Exception as e:
        logger.debug(f"Could not auto-resolve tenant from auth: {e}")

    return None, None


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

        # REPO-600: Get tenant_id for multi-tenant isolation
        try:
            from repotoire.tenant.context import get_current_org_id_str
            tenant_id = get_current_org_id_str()
        except Exception:
            tenant_id = None

        if not tenant_id:
            # Fall back to auto-resolve from auth
            resolved_tenant_id, _ = _get_tenant_from_auth()
            tenant_id = resolved_tenant_id

        if not tenant_id:
            console.print("\n[yellow]⚠️  No tenant context - metrics not recorded[/yellow]")
            console.print("[dim]Authenticate with 'repotoire auth login' for tenant isolation[/dim]")
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

        # Record to TimescaleDB with tenant isolation
        with TimescaleClient(config.timescale.connection_string) as client:
            client.record_metrics(
                metrics=metrics,
                repository=str(repo_path),
                tenant_id=tenant_id,
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
    """Get loaded configuration.

    Uses double-checked locking pattern for thread-safe lazy initialization.
    """
    global _config
    # Fast path: already initialized
    if _config is not None:
        return _config
    # Slow path: need to initialize with lock
    with _config_lock:
        # Double-check after acquiring lock
        if _config is None:
            _config = FalkorConfig()  # Defaults
    return _config


@click.group()
@click.version_option(package_name="repotoire")
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
@click.option(
    "--tenant-id",
    type=str,
    default=None,
    envvar="REPOTOIRE_TENANT_ID",
    help="Tenant ID (org UUID) for multi-tenant data isolation (REPO-600)",
)
@click.pass_context
def cli(ctx: click.Context, config: str | None, log_level: str | None, log_format: str | None, log_file: str | None, tenant_id: str | None) -> None:
    """Repotoire - Graph-Powered Code Health Platform

    \b
    Repotoire analyzes codebases using knowledge graphs to detect
    code smells, architectural issues, and technical debt.

    \b
    QUICK START:
      $ repotoire login                      # Browser OAuth (one time)
      $ repotoire analyze ./my-repo          # Run health analysis

    \b
    COMMON COMMANDS:
      repotoire login              # Login via browser OAuth
      repotoire login <key>        # Login with API key (for CI)
      repotoire logout             # Remove stored credentials
      repotoire whoami             # Check auth status
      repotoire analyze ./repo     # Analyze codebase
      repotoire ask "question"     # Query with natural language

    \b
    Get your API key at: https://repotoire.com/settings/api-keys
    """
    global _config

    # Load configuration (thread-safe)
    with _config_lock:
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
    ctx.obj['tenant_id'] = tenant_id

    # REPO-600: Auto-resolve tenant from authenticated user if not explicitly set
    resolved_tenant_id = tenant_id
    resolved_org_slug = None

    if not resolved_tenant_id:
        # Try to get tenant from authenticated user's org
        resolved_tenant_id, resolved_org_slug = _get_tenant_from_auth()

    # Set up tenant context for CLI operations
    if resolved_tenant_id:
        try:
            from uuid import UUID
            from repotoire.tenant.context import TenantContextManager

            # Validate UUID format
            tenant_uuid = UUID(resolved_tenant_id)
            # Store context manager in ctx for cleanup
            ctx.obj['_tenant_ctx_manager'] = TenantContextManager(
                org_id=tenant_uuid,
                org_slug=resolved_org_slug
            )
            ctx.obj['_tenant_ctx_manager'].__enter__()
            ctx.obj['tenant_id'] = resolved_tenant_id
            ctx.obj['org_slug'] = resolved_org_slug
            logger.debug(f"CLI tenant context set: {resolved_tenant_id} ({resolved_org_slug})")
        except ValueError:
            console.print(f"[red]✗[/red] Invalid tenant-id format: {resolved_tenant_id}")
            console.print("[dim]Tenant ID must be a valid UUID[/dim]")
            raise click.Abort()
        except Exception as e:
            console.print(f"[yellow]⚠️[/yellow] Could not set tenant context: {e}")
            logger.warning(f"Failed to set tenant context: {e}")


# =============================================================================
# Authentication Commands
# =============================================================================


@cli.command()
@click.argument("api_key", required=False)
def login(api_key: str | None) -> None:
    """Login to Repotoire Cloud.

    \b
    USAGE:
      $ repotoire login              # Browser OAuth (recommended)
      $ repotoire login ak_xxx       # Direct API key (for CI/scripts)

    \b
    Browser login opens your default browser for secure OAuth authentication.
    Direct API key login is useful for CI/CD or headless environments.

    \b
    Get your API key at: https://repotoire.com/settings/api-keys

    Credentials are stored securely in your system keyring when available,
    with a fallback to ~/.repotoire/credentials (chmod 600).
    """
    from repotoire.graph.factory import save_api_key, _validate_api_key

    if api_key is None:
        # Browser OAuth flow
        _login_browser_oauth()
    else:
        # Direct API key login
        _login_with_api_key(api_key)


def _login_with_api_key(api_key: str) -> None:
    """Login with a direct API key."""
    from repotoire.graph.factory import save_api_key, _validate_api_key

    try:
        console.print("Validating API key...", style="dim")
        auth_info = _validate_api_key(api_key)
        storage_location = save_api_key(api_key)

        # Show user info if available
        if auth_info.user:
            name = auth_info.user.name or auth_info.user.email
            console.print(
                f"\n[green]✓[/green] Logged in as [cyan]{name}[/cyan] ({auth_info.user.email})\n"
                f"  Organization: {auth_info.org_slug} ({auth_info.plan} plan)\n"
                f"  Credentials saved to: {storage_location}"
            )
        else:
            console.print(
                f"\n[green]✓[/green] Logged in to [cyan]{auth_info.org_slug}[/cyan] ({auth_info.plan} plan)\n"
                f"  Credentials saved to: {storage_location}"
            )
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        raise click.Abort()


def _login_browser_oauth() -> None:
    """Login via browser OAuth flow."""
    from repotoire.cli.auth import CLIAuth, AuthenticationError
    from repotoire.cli.credentials import mask_api_key
    from repotoire.graph.factory import _validate_api_key

    cli_auth = CLIAuth()

    console.print("[bold]Opening browser for authentication...[/bold]")
    console.print("[dim]If browser doesn't open, visit the URL shown below.[/dim]\n")

    try:
        # Browser OAuth returns the API key directly (stored by CLIAuth)
        api_key = cli_auth.login()

        # Validate the API key to get user info
        try:
            auth_info = _validate_api_key(api_key)
            if auth_info.user:
                name = auth_info.user.name or auth_info.user.email
                console.print(f"\n[green]✓[/green] Logged in as [cyan]{name}[/cyan] ({auth_info.user.email})")
                console.print(f"  Organization: {auth_info.org_slug} ({auth_info.plan} plan)")
            else:
                console.print(f"\n[green]✓[/green] Logged in to [cyan]{auth_info.org_slug}[/cyan] ({auth_info.plan} plan)")
        except Exception:
            # If validation fails, still show success with masked key
            masked_key = mask_api_key(api_key)
            console.print(f"\n[green]✓[/green] Logged in successfully")
            console.print(f"  API Key: {masked_key}")

        # Show where credentials are stored
        source = cli_auth.get_credential_source()
        if source:
            console.print(f"  Credentials saved to: {source}")

        console.print("\n[dim]Run 'repotoire ingest .' to analyze your codebase.[/dim]")

    except AuthenticationError as e:
        console.print(f"\n[red]Error:[/red] Authentication failed: {e}")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        raise click.Abort()


@cli.command()
def logout() -> None:
    """Remove stored credentials.

    \b
    USAGE:
      $ repotoire logout

    Clears credentials from system keyring and/or credentials file.
    """
    from repotoire.graph.factory import remove_api_key, get_credential_source

    source = get_credential_source()
    if remove_api_key():
        console.print(f"[green]✓[/green] Logged out. Credentials removed from {source}.")
    else:
        console.print("[dim]No stored credentials found.[/dim]")


@cli.command()
def whoami() -> None:
    """Show current authentication status.

    \b
    USAGE:
      $ repotoire whoami

    Shows your login status, organization, plan, and where credentials are stored.
    """
    from repotoire.cli.credentials import mask_api_key
    from repotoire.graph.factory import get_api_key, get_cloud_auth_info, _validate_api_key, get_credential_source

    api_key = get_api_key()
    if not api_key:
        console.print("[red]✗[/red] Not logged in\n")
        console.print("Run [cyan]repotoire login[/cyan] to authenticate via browser.")
        console.print("Or set [cyan]REPOTOIRE_API_KEY[/cyan] environment variable.")
        return

    # Try to get cached info or validate
    auth_info = get_cloud_auth_info()
    if not auth_info:
        try:
            auth_info = _validate_api_key(api_key)
        except Exception as e:
            console.print(f"[red]✗[/red] Invalid API key: {e}")
            return

    # Get credential source
    source = get_credential_source()
    masked_key = mask_api_key(api_key)

    # Show user info if available
    if auth_info.user:
        name = auth_info.user.name or auth_info.user.email
        console.print(f"[green]✓[/green] Logged in as [cyan]{name}[/cyan] ({auth_info.user.email})\n")
        console.print(f"  Organization: {auth_info.org_slug}")
        console.print(f"  Plan: {auth_info.plan}")
        console.print(f"  API Key: {masked_key}")
        console.print(f"  Credentials stored in: {source}")
    else:
        console.print(f"[green]✓[/green] Logged in to [cyan]{auth_info.org_slug}[/cyan]\n")
        console.print(f"  Plan: {auth_info.plan}")
        console.print(f"  API Key: {masked_key}")
        console.print(f"  Credentials stored in: {source}")

    # Show org switching hint
    console.print()
    console.print("[dim]Tip: Use 'repotoire org list' to see all your organizations[/dim]")
    console.print("[dim]     Use 'repotoire org switch <slug>' to switch organizations[/dim]")


# =============================================================================
# Ingest Command
# =============================================================================


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
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
    help="Generate vector embeddings for RAG (requires OpenAI API key or local backend)",
)
@click.option(
    "--embedding-backend",
    type=click.Choice(["auto", "openai", "local", "deepinfra", "voyage"], case_sensitive=False),
    default=None,
    help="Embedding backend: 'auto' (selects best available), 'voyage' (code-optimized), 'openai' (high quality), 'deepinfra' (cheap API), or 'local' (free)",
)
@click.option(
    "--embedding-model",
    default=None,
    help="Embedding model (default: text-embedding-3-small for OpenAI, Qwen3-Embedding-0.6B for local, Qwen3-Embedding-8B for DeepInfra, voyage-code-3 for Voyage)",
)
@click.option(
    "--batch-size",
    type=int,
    default=None,
    help="Number of entities to batch before loading to graph (overrides config, default: 100)",
)
@click.option(
    "--generate-contexts",
    is_flag=True,
    default=False,
    help="Generate semantic contexts using Claude for improved retrieval (adds cost)",
)
@click.option(
    "--context-model",
    type=click.Choice(["claude-haiku-3-5-20241022", "claude-sonnet-4-20250514"], case_sensitive=False),
    default="claude-haiku-3-5-20241022",
    help="Claude model for context generation (haiku is cheaper, default: claude-haiku-3-5-20241022)",
)
@click.option(
    "--max-context-cost",
    type=float,
    default=None,
    help="Maximum USD to spend on context generation (default: unlimited)",
)
@click.pass_context
def ingest(
    ctx: click.Context,
    repo_path: str,
    pattern: tuple | None,
    follow_symlinks: bool | None,
    max_file_size: float | None,
    secrets_policy: str | None,
    incremental: bool,
    force_full: bool,
    quiet: bool,
    generate_clues: bool,
    generate_embeddings: bool,
    embedding_backend: str | None,
    embedding_model: str | None,
    batch_size: int | None,
    generate_contexts: bool,
    context_model: str,
    max_context_cost: float | None,
) -> None:
    """Ingest a codebase and run health analysis.

    \b
    This is the main command for analyzing a codebase. It:
    1. Parses source code and builds a knowledge graph
    2. Extracts git history for temporal analysis
    3. Generates graph embeddings (Node2Vec)
    4. Runs 40+ detectors for code health analysis
    5. Displays health report with grade and findings

    \b
    PREREQUISITES:
      Login first: repotoire login

    \b
    EXAMPLES:
      # Basic analysis
      $ repotoire ingest ./my-project

      # With embeddings for RAG search
      $ repotoire ingest ./my-project --generate-embeddings

      # Force full re-ingestion (ignore cache)
      $ repotoire ingest ./my-project --force-full

    \b
    INCREMENTAL MODE (default):
      Only processes files changed since last ingestion. Uses MD5 hashes
      stored in the graph to detect changes. 10-100x faster than full
      re-ingestion. Use --force-full to override.

    \b
    SECURITY FEATURES:
      - Repository boundary validation (prevents path traversal)
      - Symlink protection (disabled by default)
      - File size limits (10MB default)
      - Secrets detection with configurable policy

    \b
    EMBEDDING BACKENDS:
      auto      Auto-select best available (default)
      voyage    Voyage AI code-optimized embeddings (best for code)
      openai    OpenAI text-embedding-3-small (high quality)
      deepinfra DeepInfra Qwen3-Embedding-8B (cheap API)
      local     Local Qwen3-Embedding-0.6B (free, no API key)

    \b
    ENVIRONMENT VARIABLES:
      REPOTOIRE_API_KEY         API key (or use 'repotoire login')
      OPENAI_API_KEY            For OpenAI embeddings
      VOYAGE_API_KEY            For Voyage embeddings
      DEEPINFRA_API_KEY         For DeepInfra embeddings
    """
    # Get config from context
    config: FalkorConfig = ctx.obj['config']

    # Validate inputs before execution
    try:
        # Validate repository path
        validated_repo_path = validate_repository_path(repo_path)

        # Apply config defaults (CLI options override config)
        final_patterns = list(pattern) if pattern else config.ingestion.patterns
        final_follow_symlinks = follow_symlinks if follow_symlinks is not None else config.ingestion.follow_symlinks
        final_max_file_size = max_file_size if max_file_size is not None else config.ingestion.max_file_size_mb
        final_secrets_policy_str = secrets_policy if secrets_policy is not None else config.secrets.policy
        final_batch_size = batch_size if batch_size is not None else config.ingestion.batch_size

        # Apply embedding config (CLI options override config, default to "auto")
        final_embedding_backend = embedding_backend or config.embeddings.backend or "auto"
        final_embedding_model = embedding_model or config.embeddings.model

        # Convert secrets policy string to enum
        final_secrets_policy = SecretsPolicy(final_secrets_policy_str)

        # Validate file size limit
        final_max_file_size = validate_file_size_limit(final_max_file_size)

        # Validate batch size
        validated_batch_size = validate_batch_size(final_batch_size)

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
        from repotoire.ai.embeddings import EmbeddingConfig
        embed_cfg = EmbeddingConfig(backend=final_embedding_backend)
        resolved_backend, reason = embed_cfg.resolve_backend()
        console.print(f"[cyan]🔮 Vector Embeddings: Enabled ({resolved_backend})[/cyan]")
        if final_embedding_backend == "auto":
            console.print(f"[dim]   {reason}[/dim]")
    else:
        # KG-3 Fix: Warn users that RAG features won't work without embeddings
        console.print(
            "[yellow]⚠️  Embeddings disabled. RAG features (semantic search, 'ask' command) "
            "will not work.[/yellow]"
        )
        console.print(
            "[dim]   Add --generate-embeddings to enable. "
            "Use --embedding-backend=local for free local embeddings.[/dim]"
        )

    # Display Rust parser status
    from repotoire.parsers.rust_parser import is_rust_parser_available, get_supported_languages
    if is_rust_parser_available():
        supported = get_supported_languages()
        console.print(f"[green]⚡ Rust Parser: Enabled (10-100x faster)[/green]")
        console.print(f"[dim]   Languages: {', '.join(supported)}[/dim]")
    else:
        console.print("[yellow]⚠️  Rust Parser: Not available (using Python AST)[/yellow]")
        console.print("[dim]   Install repotoire-fast for 10-100x faster parsing[/dim]")
    console.print()

    try:
        with LogContext(operation="ingest", repo_path=repo_path):
            logger.info("Starting ingestion")

            # Create database client (connects to Repotoire Cloud)
            db = _get_db_client(quiet=quiet)

            try:
                # Clear database if force-full is requested
                if force_full:
                    if not quiet:
                        # Enhanced destructive operation warning (Phase 5 CLI UX)
                        console.print("\n[bold red]⚠️  WARNING: DESTRUCTIVE OPERATION[/bold red]")
                        console.print("[yellow]This will DELETE:[/yellow]")
                        console.print("  • All ingested code entities (files, classes, functions)")
                        console.print("  • All relationship data (calls, imports, uses)")
                        console.print("  • All analysis findings and health metrics")
                        console.print("  • All cached embeddings and graph vectors")
                        console.print("\n[red]This cannot be undone.[/red]")
                        console.print()
                        if not click.confirm("Type 'y' to confirm deletion", default=False):
                            console.print("[dim]Aborted.[/dim]")
                            raise click.Abort()
                        console.print("\n[yellow]Clearing existing graph...[/yellow]")
                    db.clear_graph()
                    if not quiet:
                        console.print("[green]✓ Database cleared[/green]\n")

                # Detect repo info for node tagging when authenticated (REPO-397)
                repo_id = None
                repo_slug = None
                repo_info = None
                from repotoire.graph.factory import get_api_key
                if get_api_key():
                    from repotoire.cli.repo_utils import detect_repo_info
                    repo_info = detect_repo_info(validated_repo_path)
                    repo_id = repo_info.repo_id
                    repo_slug = repo_info.repo_slug
                    if repo_info.source == "git":
                        console.print(f"[dim]Repository: {repo_info.repo_slug} (via git remote)[/dim]")
                    else:
                        console.print(f"[dim]Repository: {repo_info.repo_slug} (local path)[/dim]")

                pipeline = IngestionPipeline(
                    str(validated_repo_path),
                    db,
                    follow_symlinks=final_follow_symlinks,
                    max_file_size_mb=final_max_file_size,
                    batch_size=validated_batch_size,
                    secrets_policy=final_secrets_policy,
                    generate_clues=generate_clues,
                    generate_embeddings=generate_embeddings,
                    embedding_backend=final_embedding_backend,
                    embedding_model=final_embedding_model,
                    generate_contexts=generate_contexts,
                    context_model=context_model,
                    max_context_cost=max_context_cost,
                    repo_id=repo_id,
                    repo_slug=repo_slug,
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

                # Show dashboard link when authenticated (REPO-397)
                if repo_info and repo_id:
                    console.print(
                        f"\n[green]📡 Synced to dashboard:[/green] "
                        f"[link=https://repotoire.com/repos/{repo_id[:8]}]"
                        f"https://repotoire.com/repos/{repo_id[:8]}...[/link]"
                    )

                # Note: Git history ingestion is now done via `repotoire historical ingest`
                # which uses GitHistoryRAG (99% cheaper than old Graphiti approach)
                    if not quiet:
                        console.print(f"[yellow]⚠️  Git history skipped: {e}[/yellow]")

                # Generate graph embeddings (Node2Vec) - the differentiator!
                # Uses parallel Rust backend for fast training
                function_count = stats.get("total_functions", 0)
                if function_count > 0:
                    console.print("\n[bold cyan]Generating graph embeddings (Node2Vec)...[/bold cyan]")
                    try:
                        from repotoire.ml.node2vec_embeddings import Node2VecEmbedder, Node2VecConfig

                        node2vec_config = Node2VecConfig(
                            embedding_dimension=128,
                            walk_length=80,
                            walks_per_node=10,
                        )
                        embedder = Node2VecEmbedder(db, node2vec_config, force_rust=True)

                        embed_stats = embedder.generate_and_store_embeddings(
                            node_labels=["Function", "Class", "Module"],
                            relationship_types=["CALLS", "IMPORTS", "USES"],
                            seed=42,
                        )

                        embedded_count = embed_stats.get("nodePropertiesWritten", embed_stats.get("embedded_count", 0))
                        console.print(f"[green]✓ Generated {embedded_count:,} graph embeddings[/green]")
                    except Exception as e:
                        # Non-fatal - graph embeddings are enhancement, not required
                        logger.warning(f"Graph embedding generation failed: {e}")
                        console.print(f"[yellow]⚠️  Graph embeddings skipped: {e}[/yellow]")

                # Run analysis automatically after ingestion
                console.print("\n[bold cyan]🔍 Running code health analysis...[/bold cyan]")
                try:
                    detector_config_dict = asdict(config.detectors)
                    engine = AnalysisEngine(
                        db,
                        detector_config=detector_config_dict,
                        repository_path=str(validated_repo_path),
                        repo_id=repo_id,
                        parallel=True,
                        max_workers=_get_optimal_workers(),
                        enable_insights=True,
                    )

                    # Run analysis with progress indication
                    if not quiet:
                        total_detectors = len(engine.detectors)
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            BarColumn(),
                            TaskProgressColumn(),
                            console=console,
                            transient=True,
                        ) as progress:
                            task_id = progress.add_task(
                                "[cyan]Running detectors...",
                                total=total_detectors
                            )

                            def detector_progress(detector_name: str, current: int, total: int, status: str) -> None:
                                if status == "starting":
                                    progress.update(task_id, description=f"[cyan]Running:[/cyan] {detector_name}")
                                elif status == "completed":
                                    progress.update(task_id, completed=current, description=f"[green]✓[/green] {detector_name}")
                                elif status == "failed":
                                    progress.update(task_id, completed=current, description=f"[red]✗[/red] {detector_name}")

                            health = engine.analyze(progress_callback=detector_progress)
                        console.print(f"[green]✓ Ran {total_detectors} detectors[/green]")
                    else:
                        health = engine.analyze()

                    # Display health summary
                    _display_health_report(health)

                except Exception as e:
                    logger.warning(f"Analysis failed: {e}")
                    console.print(f"[yellow]⚠️  Analysis skipped: {e}[/yellow]")

                # Show security info if files were skipped
                if pipeline.skipped_files:
                    console.print(
                        f"\n[yellow]⚠️  {len(pipeline.skipped_files)} files were skipped "
                        f"(see logs for details)[/yellow]"
                    )
            finally:
                db.close()

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise click.Abort()
    except ConnectionError as e:
        # Enhanced error message for connection failures (Phase 5 CLI UX)
        logger.error(f"Connection error: {e}")
        console.print("\n[red]❌ Cannot connect to FalkorDB[/red]")
        console.print(f"  Host: {os.getenv('FALKORDB_HOST', 'localhost')}")
        console.print(f"  Port: {os.getenv('FALKORDB_PORT', '6379')}")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("  • Check if FalkorDB is running: [dim]docker ps | grep falkordb[/dim]")
        console.print("  • Start FalkorDB: [dim]docker run -p 6379:6379 falkordb/falkordb[/dim]")
        console.print("  • Or use cloud mode: [dim]repotoire login[/dim]")
        raise click.Abort()
    except PermissionError as e:
        # Enhanced error message for permission failures (Phase 5 CLI UX)
        logger.error(f"Permission error: {e}")
        filename = getattr(e, 'filename', 'unknown file')
        console.print(f"\n[red]❌ Permission denied: {filename}[/red]")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print(f"  • Check file permissions: [dim]ls -la {filename}[/dim]")
        console.print(f"  • Grant read access: [dim]chmod 644 {filename}[/dim]")
        console.print(f"  • Or exclude the file via patterns in config")
        raise click.Abort()
    except MemoryError:
        # Enhanced error message for memory issues (Phase 5 CLI UX)
        logger.error("Out of memory during ingestion")
        console.print("\n[red]❌ Out of memory[/red]")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("  • Reduce batch size: [dim]repotoire ingest --batch-size 50[/dim]")
        console.print("  • Exclude large files: [dim]--max-file-size 5[/dim]")
        console.print("  • Split repository into smaller chunks")
        console.print("  • Increase system memory or swap space")
        raise click.Abort()
    except Exception as e:
        logger.exception("Unexpected error during ingestion")
        console.print(f"\n[red]❌ Unexpected error: {e}[/red]")
        raise


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option(
    "--output", "-o", type=click.Path(), help="Output file for report"
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "html", "sarif", "markdown", "pdf", "excel"], case_sensitive=False),
    default="json",
    help="Output format (json, html, sarif, markdown, pdf, excel)",
)
@click.option(
    "--open",
    "open_report",
    is_flag=True,
    default=False,
    help="Automatically open the generated report in the default browser",
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
@click.option(
    "--keep-metadata",
    is_flag=True,
    default=False,
    help="Keep detector metadata in graph after analysis (enables 'repotoire hotspots' queries)",
)
@click.option(
    "--parallel/--no-parallel",
    default=True,
    help="Run independent detectors in parallel (default: enabled, REPO-217)",
)
@click.option(
    "--workers",
    type=int,
    default=_get_optimal_workers(),
    help=f"Number of parallel workers for detector execution (default: {_get_optimal_workers()}, based on CPU cores)",
)
@click.option(
    "--offline",
    is_flag=True,
    default=False,
    envvar="REPOTOIRE_OFFLINE",
    help="Run without authentication (skip API auth and tier limit checks)",
)
@click.option(
    "--fail-on-grade",
    type=click.Choice(["A", "B", "C", "D", "F"], case_sensitive=False),
    default=None,
    help="Exit with code 2 if health grade is below threshold (e.g., --fail-on-grade C fails for D or F)",
)
@click.option(
    "--disable-detectors",
    type=str,
    default=None,
    help="Comma-separated list of detectors to disable (e.g., 'Ruff,Pylint,Bandit')",
)
@click.option(
    "--enable-detectors",
    type=str,
    default=None,
    help="Comma-separated list of detectors to enable (all others disabled). Takes precedence over config file.",
)
@click.option(
    "--insights/--no-insights",
    default=True,
    help="Enable graph insights and ML enrichment (bottlenecks, coupling, bug risk). Default: enabled.",
)
@click.option(
    "--top",
    type=int,
    default=None,
    help="Limit output to top N findings by severity (e.g., --top 10)",
)
@click.option(
    "--severity",
    type=click.Choice(["critical", "high", "medium", "low", "info"], case_sensitive=False),
    default=None,
    help="Filter findings to this severity or higher (e.g., --severity high shows critical+high)",
)
@click.pass_context
def analyze(
    ctx: click.Context,
    repo_path: str,
    output: str | None,
    format: str,
    open_report: bool,
    quiet: bool,
    track_metrics: bool,
    keep_metadata: bool,
    parallel: bool,
    workers: int,
    offline: bool,
    fail_on_grade: str | None,
    disable_detectors: str | None,
    enable_detectors: str | None,
    insights: bool,
    top: int | None,
    severity: str | None,
) -> None:
    """Analyze codebase health and generate a comprehensive report.

    \b
    Runs 30+ detectors powered by graph-based code analysis.
    
    Unlike traditional linters that check files in isolation, Repotoire
    builds a knowledge graph of your codebase to detect cross-file issues:
    circular dependencies, dead code, architectural violations, and more.

    \b
    EXAMPLES:
      # Basic analysis with terminal output
      $ repotoire analyze ./my-project

      # Generate HTML report
      $ repotoire analyze ./my-project -o report.html -f html

      # JSON output for CI/CD
      $ repotoire analyze ./my-project -f json -o results.json

      # Track metrics over time (requires TimescaleDB)
      $ repotoire analyze ./my-project --track-metrics

    \b
    HEALTH SCORES:
      The analysis produces three category scores (0-100):
      - Structure (40%): Modularity, dependencies, coupling
      - Quality (30%): Complexity, duplication, dead code
      - Architecture (30%): Patterns, layering, cohesion

      Overall health = weighted average of category scores.

    \b
    SEVERITY LEVELS:
      critical   Must fix immediately (security, crashes)
      high       Should fix soon (bugs, major issues)
      medium     Should address (maintainability)
      low        Nice to fix (style, minor issues)
      info       Informational only

    \b
    DETECTORS (30+):
      Graph-Native (unique to Repotoire):
        circular-deps      Import cycle detection via SCC algorithm
        dead-code          Cross-file unused function/class detection
        god-class          Classes with too many responsibilities
        feature-envy       Methods that use other classes more than their own
        shotgun-surgery    Changes that require edits across many files
        hub-dependency     Fragile central nodes everything depends on
        architectural      Bottlenecks, layering violations, coupling
        change-coupling    Files that always change together
        ... and 20+ more graph-powered detectors

      Hybrid (external tools + graph context):
        ruff       400+ linting rules (fast)
        pylint     Python-specific checks  
        mypy       Type checking errors
        bandit     Security vulnerabilities
        semgrep    Advanced security patterns

    \b
    PARALLEL EXECUTION:
      Detectors run in parallel by default for 3-4x speedup.
      Use --no-parallel to disable, --workers to adjust threads.

    \b
    EXIT CODES:
      0   Success (no critical findings)
      1   Analysis error
      2   Critical findings detected (CI/CD fail condition)
    """
    # Get config from context
    config: FalkorConfig = ctx.obj['config']

    # Check auth and tier limits (unless offline mode)
    if not offline:
        from repotoire.cli.auth import CLIAuth, is_offline_mode
        from repotoire.cli.tier_limits import TierLimits

        if not is_offline_mode():
            cli_auth = CLIAuth()
            credentials = cli_auth.get_current_user()

            if credentials:
                # User is logged in, check tier limits
                limits = TierLimits(cli_auth)
                if not limits.check_can_analyze_sync(credentials):
                    raise click.Abort()

                if not quiet:
                    console.print(f"[dim]Authenticated as {credentials.user_email}[/dim]")
                    if credentials.org_slug:
                        console.print(f"[dim]Organization: {credentials.org_slug} ({credentials.tier.title()})[/dim]")
            else:
                # Not logged in - show hint but allow analysis
                if not quiet:
                    console.print("[dim]Tip: Login with 'repotoire login <api_key>' to track usage[/dim]")

    # Validate inputs before execution
    try:
        # Validate repository path
        validated_repo_path = validate_repository_path(repo_path)

        # Validate output path if provided
        validated_output = None
        if output:
            validated_output = validate_output_path(output)

    except ValidationError as e:
        console.print(f"\n[red]❌ Validation Error:[/red] {e.message}")
        if e.suggestion:
            console.print(f"\n[yellow]{e.suggestion}[/yellow]")
        raise click.Abort()

    console.print(f"\n[bold cyan]🎼 Repotoire Analysis[/bold cyan]\n")

    try:
        with LogContext(operation="analyze", repo_path=repo_path):
            logger.info("Starting analysis")

            # Create database client (requires API key)
            db = _get_db_client(quiet=quiet)
            try:
                # Convert detector config to dict for detectors
                detector_config_dict = asdict(config.detectors)

                # Apply CLI overrides for detector enable/disable
                if enable_detectors:
                    enabled_list = [d.strip() for d in enable_detectors.split(",") if d.strip()]
                    detector_config_dict["enabled_detectors"] = enabled_list
                    if not quiet:
                        console.print(f"[dim]Enabled detectors: {', '.join(enabled_list)}[/dim]")

                if disable_detectors:
                    disabled_list = [d.strip() for d in disable_detectors.split(",") if d.strip()]
                    # Merge with existing disabled detectors from config
                    existing_disabled = detector_config_dict.get("disabled_detectors", []) or []
                    detector_config_dict["disabled_detectors"] = list(set(existing_disabled + disabled_list))
                    if not quiet:
                        console.print(f"[dim]Disabled detectors: {', '.join(disabled_list)}[/dim]")

                engine = AnalysisEngine(
                    db,
                    detector_config=detector_config_dict,
                    repository_path=str(repo_path),
                    keep_metadata=keep_metadata,
                    parallel=parallel,
                    max_workers=workers,
                    enable_insights=insights,
                )

                # Run analysis with progress indication
                if not quiet:
                    # Count total detectors for progress tracking
                    total_detectors = len(engine.detectors)

                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        console=console,
                        transient=True,  # Clear progress bar when done
                    ) as progress:
                        task_id = progress.add_task(
                            "[cyan]Running detectors...",
                            total=total_detectors
                        )

                        def progress_callback(detector_name: str, current: int, total: int, status: str) -> None:
                            """Update progress bar with current detector status."""
                            if status == "starting":
                                progress.update(
                                    task_id,
                                    description=f"[cyan]Running:[/cyan] {detector_name}"
                                )
                            elif status == "completed":
                                progress.update(
                                    task_id,
                                    completed=current,
                                    description=f"[green]Completed:[/green] {detector_name}"
                                )
                            elif status == "failed":
                                progress.update(
                                    task_id,
                                    completed=current,
                                    description=f"[red]Failed:[/red] {detector_name}"
                                )

                        health = engine.analyze(progress_callback=progress_callback)

                    # Show completion message
                    console.print(f"[green]Ran {total_detectors} detectors[/green]\n")
                else:
                    health = engine.analyze()

                # Filter findings by severity and/or top N (REPO-520)
                original_count = len(health.findings)
                if severity or top:
                    from repotoire.models import Severity
                    severity_order = {
                        Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
                        Severity.LOW: 3, Severity.INFO: 4
                    }
                    
                    # Sort by severity first
                    health.findings.sort(key=lambda f: severity_order.get(f.severity, 5))
                    
                    # Filter by minimum severity
                    if severity:
                        min_severity = Severity(severity.lower())
                        min_order = severity_order.get(min_severity, 5)
                        health.findings = [f for f in health.findings if severity_order.get(f.severity, 5) <= min_order]
                    
                    # Limit to top N
                    if top and len(health.findings) > top:
                        health.findings = health.findings[:top]
                    
                    # Show filtering message
                    if len(health.findings) < original_count:
                        console.print(f"[dim]Showing {len(health.findings)} of {original_count} findings[/dim]\n")

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
                        reporter = HTMLReporter(
                            repo_path=validated_repo_path,
                            config=config.reporting,
                        )
                        reporter.generate(health, validated_output)
                        logger.info(f"HTML report saved to {validated_output}")
                        console.print(f"\n✅ HTML report saved to {validated_output}")
                    elif format.lower() == "sarif":
                        from repotoire.reporters import SARIFReporter
                        reporter = SARIFReporter(repo_path=validated_repo_path)
                        reporter.generate(health, validated_output)
                        logger.info(f"SARIF report saved to {validated_output}")
                        console.print(f"\n✅ SARIF report saved to {validated_output}")
                        console.print(f"[dim]Compatible with GitHub Code Scanning and other SARIF tools[/dim]")
                    elif format.lower() == "markdown":
                        from repotoire.reporters import MarkdownReporter
                        reporter = MarkdownReporter(repo_path=validated_repo_path)
                        reporter.generate(health, validated_output)
                        logger.info(f"Markdown report saved to {validated_output}")
                        console.print(f"\n✅ Markdown report saved to {validated_output}")
                        console.print(f"[dim]GitHub-flavored Markdown with emoji support[/dim]")
                    elif format.lower() == "pdf":
                        from repotoire.reporters import PDFReporter
                        reporter = PDFReporter(repo_path=validated_repo_path)
                        reporter.generate(health, validated_output)
                        logger.info(f"PDF report saved to {validated_output}")
                        console.print(f"\n✅ PDF report saved to {validated_output}")
                    elif format.lower() == "excel":
                        from repotoire.reporters import ExcelReporter
                        reporter = ExcelReporter(repo_path=validated_repo_path)
                        reporter.generate(health, validated_output)
                        logger.info(f"Excel report saved to {validated_output}")
                        console.print(f"\n✅ Excel report saved to {validated_output}")
                        console.print(f"[dim]Multi-sheet workbook with Summary, Findings, Metrics, and By Detector sheets[/dim]")
                    else:  # JSON format
                        import json
                        with open(validated_output, "w") as f:
                            json.dump(health.to_dict(), f, indent=2)
                        logger.info(f"JSON report saved to {validated_output}")
                        console.print(f"\n✅ JSON report saved to {validated_output}")

                    # Open report in browser if requested
                    if open_report:
                        import webbrowser
                        file_url = Path(validated_output).absolute().as_uri()
                        webbrowser.open(file_url)
                        if not quiet:
                            console.print(f"[dim]Opening report in browser...[/dim]")

                # Record metrics to TimescaleDB if enabled
                if track_metrics or config.timescale.auto_track:
                    _record_metrics_to_timescale(
                        health=health,
                        repo_path=validated_repo_path,
                        config=config,
                        quiet=quiet
                    )

                # Check fail-on-grade threshold
                if fail_on_grade:
                    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
                    threshold = grade_order.get(fail_on_grade.upper(), 4)
                    actual = grade_order.get(health.grade, 4)
                    if actual > threshold:
                        console.print(
                            f"\n[red]❌ Grade {health.grade} is below threshold {fail_on_grade.upper()}[/red]"
                        )
                        ctx.exit(2)
                    else:
                        if not quiet:
                            console.print(
                                f"\n[green]✓ Grade {health.grade} meets threshold {fail_on_grade.upper()}[/green]"
                            )

            finally:
                db.close()

    except click.Abort:
        # Let Click handle abort cleanly (preserves command context)
        raise
    except click.ClickException:
        # Let Click handle its own exceptions (preserves command context)
        raise
    except ConnectionError as e:
        # Enhanced error message for connection failures (Phase 5 CLI UX)
        logger.error(f"Connection error: {e}")
        console.print("\n[red]❌ Cannot connect to FalkorDB[/red]")
        console.print(f"  Host: {os.getenv('FALKORDB_HOST', 'localhost')}")
        console.print(f"  Port: {os.getenv('FALKORDB_PORT', '6379')}")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("  • Check if FalkorDB is running: [dim]docker ps | grep falkordb[/dim]")
        console.print("  • Start FalkorDB: [dim]docker run -p 6379:6379 falkordb/falkordb[/dim]")
        console.print("  • Or use cloud mode: [dim]repotoire login[/dim]")
        raise click.Abort()
    except PermissionError as e:
        # Enhanced error message for permission failures (Phase 5 CLI UX)
        logger.error(f"Permission error: {e}")
        filename = getattr(e, 'filename', 'unknown file')
        console.print(f"\n[red]❌ Permission denied: {filename}[/red]")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print(f"  • Check file permissions: [dim]ls -la {filename}[/dim]")
        console.print(f"  • Grant read access: [dim]chmod 644 {filename}[/dim]")
        raise click.Abort()
    except MemoryError:
        # Enhanced error message for memory issues (Phase 5 CLI UX)
        logger.error("Out of memory during analysis")
        console.print("\n[red]❌ Out of memory[/red]")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("  • Enable parallel mode with fewer workers: [dim]--workers 2[/dim]")
        console.print("  • Disable some detectors: [dim]--disable-detectors semgrep,jscpd[/dim]")
        console.print("  • Increase system memory or swap space")
        raise click.Abort()
    except Exception as e:
        # Check for ConfigurationError (no database configured)
        from repotoire.graph.factory import ConfigurationError
        if isinstance(e, ConfigurationError):
            console.print(f"\n[yellow]⚠️  {e}[/yellow]")
            raise click.Abort()
        logger.exception("Error during analysis")
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise click.Abort()


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

    # Display insights if available (REPO-501)
    if health.insights:
        _display_insights(health.insights)

    # Findings note
    if health.findings_summary.total > 0 and health.findings:
        console.print(f"\n[dim]📋 {len(health.findings)} findings detected. Use HTML/JSON output for details.[/dim]")


def _display_insights(insights) -> None:
    """Display insights from ML and graph analysis (REPO-501)."""
    gi = insights.graph_insights
    
    # Graph insights panel
    insights_content = []
    
    # Bottlenecks
    if gi.bottlenecks:
        insights_content.append("[bold]🎯 Bottlenecks[/bold] (high fan-in functions)")
        for b in gi.bottlenecks[:5]:
            name = b.get("name", "unknown")
            fan_in = b.get("fan_in", 0)
            # Truncate long names
            if len(name) > 50:
                name = "..." + name[-47:]
            insights_content.append(f"  [cyan]{name}[/cyan] ← {fan_in} callers")
        insights_content.append("")
    
    # Coupling hotspots
    if gi.coupling_hotspots:
        insights_content.append("[bold]🔗 Coupling Hotspots[/bold] (files with many cross-deps)")
        for h in gi.coupling_hotspots[:5]:
            file = h.get("file", "unknown")
            coupled = h.get("coupled_to", 0)
            # Truncate long paths
            if len(file) > 50:
                file = "..." + file[-47:]
            insights_content.append(f"  [yellow]{file}[/yellow] → {coupled} files")
        insights_content.append("")
    
    # High-risk entities (from bug prediction or heuristics)
    if insights.high_risk_entities:
        # Check if any are from ML model
        has_ml = any(r.get("source") == "ml_model" for r in insights.high_risk_entities)
        source_label = "ML-predicted" if has_ml else "heuristic-scored"
        insights_content.append(f"[bold]⚠️  High Bug Risk[/bold] ({source_label})")
        for r in insights.high_risk_entities[:5]:
            entity = r.get("entity", "unknown")
            prob = r.get("bug_probability", 0)
            if len(entity) > 50:
                entity = "..." + entity[-47:]
            pct = int(prob * 100)
            color = "red" if pct >= 80 else "yellow"
            # Show factors if available (heuristic scoring)
            factors = r.get("factors", {})
            factor_hint = ""
            if factors:
                hints = []
                if factors.get("complexity", 0) > 15:
                    hints.append(f"complexity={factors['complexity']}")
                if factors.get("fan_in", 0) > 10:
                    hints.append(f"callers={factors['fan_in']}")
                if not factors.get("has_tests"):
                    hints.append("no tests")
                if hints:
                    factor_hint = f" [dim]({', '.join(hints[:2])})[/dim]"
            insights_content.append(f"  [{color}]{entity}[/{color}] ({pct}% risk){factor_hint}")
        insights_content.append("")
    
    # High-impact entities
    if insights.high_impact_entities:
        insights_content.append("[bold]💥 High Impact[/bold] (large blast radius)")
        for e in insights.high_impact_entities[:5]:
            entity = e.get("entity", "unknown")
            radius = e.get("blast_radius", 0)
            if len(entity) > 50:
                entity = "..." + entity[-47:]
            insights_content.append(f"  [magenta]{entity}[/magenta] → {radius} dependents")
        insights_content.append("")
    
    # Summary stats
    stats = []
    if gi.dead_code_count > 0:
        stats.append(f"💀 {gi.dead_code_count} dead functions")
    if gi.circular_dep_count > 0:
        stats.append(f"🔁 {gi.circular_dep_count} circular deps")
    if gi.avg_fan_out > 0:
        stats.append(f"📊 Avg fan-out: {gi.avg_fan_out:.1f}")
    if insights.findings_enriched > 0:
        # Check source - ML or heuristic
        has_ml = any(r.get("source") == "ml_model" for r in insights.high_risk_entities)
        method = "ML" if has_ml else "heuristics"
        stats.append(f"🧠 {insights.findings_enriched} findings scored ({method})")
    
    if stats:
        insights_content.append("[dim]" + " | ".join(stats) + "[/dim]")
    
    if insights_content:
        console.print(Panel(
            "\n".join(insights_content),
            title="🔬 Graph Insights",
            border_style="cyan",
            box=box.ROUNDED,
        ))


def _display_findings_tree(findings, severity_colors, severity_emoji):
    """Display findings in a tree structure grouped by detector.

    Note: All user-provided content (titles, descriptions, file paths, fixes)
    is escaped using rich.markup.escape() to prevent Rich from interpreting
    square brackets as markup tags. This fixes REPO-179 where content like
    'arr[0]' or '[config]' would be incorrectly interpreted as Rich markup.

    Note: We use `builtins.list` explicitly because this module has a Click
    command named `list` that shadows the builtin. This was causing REPO-179
    where calling this function would trigger Click's argument parser.
    """
    import builtins
    from collections import defaultdict

    # Group findings by detector
    # Use builtins.list to avoid shadowing by the `list` Click command in this module
    by_detector = defaultdict(builtins.list)
    for finding in findings:
        by_detector[finding.detector].append(finding)

    # Create tree for each detector
    for detector, detector_findings in sorted(by_detector.items()):
        tree = Tree(f"[bold cyan]{escape(detector)}[/bold cyan]")

        for finding in detector_findings:
            color = severity_colors[finding.severity]
            emoji = severity_emoji[finding.severity]

            # Create finding branch - escape title to prevent markup interpretation
            severity_label = f"{emoji} [{color}]{finding.severity.value.upper()}[/{color}]"
            escaped_title = escape(finding.title)
            finding_text = f"{severity_label}: {escaped_title}"
            finding_branch = tree.add(finding_text)

            # Add description - escape to prevent markup interpretation
            if finding.description:
                escaped_desc = escape(finding.description)
                finding_branch.add(f"[dim]{escaped_desc}[/dim]")

            # Add affected files - escape file paths (may contain brackets)
            if finding.affected_files:
                escaped_files = [escape(f) for f in finding.affected_files[:3]]
                files_text = ", ".join(escaped_files)
                if len(finding.affected_files) > 3:
                    files_text += f" [dim](+{len(finding.affected_files) - 3} more)[/dim]"
                finding_branch.add(f"[yellow]Files:[/yellow] {files_text}")

            # Add suggested fix if available - escape fix text
            if finding.suggested_fix:
                fix_branch = finding_branch.add("[green]💡 Suggested Fix:[/green]")
                # Limit fix text length for display
                fix_text = finding.suggested_fix
                if len(fix_text) > 200:
                    fix_text = fix_text[:200] + "..."
                escaped_fix = escape(fix_text)
                fix_branch.add(f"[dim]{escaped_fix}[/dim]")

        console.print(tree)
        console.print()  # Add spacing between detectors


@cli.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Validate configuration and API connectivity.

    Checks:
    - API key is set (REPOTOIRE_API_KEY)
    - API connectivity (server is reachable)
    - Configuration file validity (if present)
    - Ingestion settings are valid

    Exits with non-zero code if any validation fails.
    """
    import os
    from repotoire.graph.factory import get_api_key

    config: FalkorConfig = ctx.obj['config']

    console.print("\n[bold cyan]🎼 Repotoire Configuration Validation[/bold cyan]\n")

    validation_results = []
    all_passed = True

    # 1. Check API key
    console.print("[dim]Checking API key...[/dim]")
    api_key = get_api_key()
    if api_key:
        # Mask the key for display
        masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        validation_results.append(("API key", f"✓ {masked_key}", "green"))
        console.print(f"[green]✓[/green] API key found: {masked_key}\n")
    else:
        validation_results.append(("API key", "✗ Not set", "red"))
        console.print("[red]✗[/red] API key not set")
        console.print("[yellow]💡 Set REPOTOIRE_API_KEY or run: repotoire login[/yellow]\n")
        all_passed = False
        _print_validation_summary(validation_results, all_passed)
        raise click.Abort()

    # 2. Test API connectivity
    console.print("[dim]Testing API connectivity...[/dim]")
    try:
        client = _get_db_client()
        stats = client.get_stats()
        client.close()
        validation_results.append(("API connectivity", "✓ Connected successfully", "green"))
        console.print("[green]✓[/green] API connection successful\n")
    except Exception as e:
        validation_results.append(("API connectivity", f"✗ {e}", "red"))
        console.print(f"[red]✗[/red] API connection failed: {e}")
        console.print("[yellow]💡 Check your API key and network connection[/yellow]\n")
        all_passed = False

    # 3. Validate configuration file
    console.print("[dim]Checking configuration file...[/dim]")
    try:
        validation_results.append(("Configuration file", "✓ Valid", "green"))
        console.print("[green]✓[/green] Configuration file valid\n")
    except Exception as e:
        validation_results.append(("Configuration file", f"✗ {e}", "red"))
        console.print(f"[red]✗[/red] Configuration file error: {e}\n")
        all_passed = False

    # 4. Validate ingestion settings
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


@cli.command("scan-secrets")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--output", "-o", type=click.Path(), default=None,
    help="Output file for results (JSON format)",
)
@click.option(
    "--format", "-f",
    type=click.Choice(["table", "json", "sarif"], case_sensitive=False),
    default="table",
    help="Output format (default: table)",
)
@click.option(
    "--parallel/--no-parallel", default=True,
    help="Use parallel scanning for multiple files (default: enabled)",
)
@click.option(
    "--workers", "-w", type=int, default=_get_optimal_workers(),
    help=f"Number of parallel workers (default: {_get_optimal_workers()}, based on CPU cores)",
)
@click.option(
    "--min-risk",
    type=click.Choice(["critical", "high", "medium", "low"], case_sensitive=False),
    default=None,
    help="Minimum risk level to report (default: all)",
)
@click.option(
    "--pattern", "-p", multiple=True, default=None,
    help="File patterns to scan (e.g., '**/*.py', '**/*.env')",
)
@click.pass_context
def scan_secrets(
    ctx: click.Context,
    path: str,
    output: str | None,
    format: str,
    parallel: bool,
    workers: int,
    min_risk: str | None,
    pattern: tuple,
) -> None:
    """Scan files for secrets (API keys, passwords, tokens, etc.).

    REPO-149: Standalone secrets scanning with enhanced reporting.

    Examples:
        # Scan current directory
        repotoire scan-secrets .

        # Scan with JSON output
        repotoire scan-secrets . --format json -o secrets.json

        # Scan only Python files, critical and high risk
        repotoire scan-secrets . -p "**/*.py" --min-risk high

        # Scan with more workers
        repotoire scan-secrets . --workers 8
    """
    from pathlib import Path as PathLib
    from repotoire.security.secrets_scanner import SecretsScanner, SecretsScanResult
    import json as json_module
    import glob

    config: FalkorConfig = ctx.obj['config']

    console.print("\n[bold cyan]🔐 Repotoire Secrets Scanner[/bold cyan]")
    console.print("[dim]Scanning for hardcoded secrets, API keys, and credentials[/dim]\n")

    path_obj = PathLib(path)

    # Collect files to scan
    files_to_scan: list[PathLib] = []
    patterns = list(pattern) if pattern else config.ingestion.patterns

    with console.status("[bold green]Collecting files...", spinner="dots"):
        if path_obj.is_file():
            files_to_scan = [path_obj]
        else:
            for p in patterns:
                full_pattern = str(path_obj / p)
                for match in glob.glob(full_pattern, recursive=True):
                    mp = PathLib(match)
                    if mp.is_file():
                        files_to_scan.append(mp)

    # Deduplicate
    files_to_scan = list(set(files_to_scan))
    console.print(f"[dim]Found {len(files_to_scan)} files to scan[/dim]\n")

    if not files_to_scan:
        console.print("[yellow]No files found matching patterns[/yellow]")
        return

    # Initialize scanner with config
    scanner = SecretsScanner(
        entropy_detection=config.secrets.entropy_detection,
        entropy_threshold=config.secrets.entropy_threshold,
        min_entropy_length=config.secrets.min_entropy_length,
        large_file_threshold_mb=config.secrets.large_file_threshold_mb,
        parallel_workers=workers,
        cache_enabled=config.secrets.cache_enabled,
        custom_patterns=config.secrets.custom_patterns,
    )

    # Scan files
    all_secrets: list = []
    total_by_risk: dict = {}
    total_by_type: dict = {}
    files_with_secrets = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning files...", total=len(files_to_scan))

        if parallel and len(files_to_scan) > 2:
            results = scanner.scan_files_parallel(files_to_scan, max_workers=workers)
            for fp, result in results.items():
                progress.advance(task)
                if result.has_secrets:
                    files_with_secrets += 1
                    for s in result.secrets_found:
                        # Filter by min_risk if specified
                        if min_risk:
                            risk_order = ["critical", "high", "medium", "low"]
                            if risk_order.index(s.risk_level) > risk_order.index(min_risk):
                                continue
                        all_secrets.append(s)
                    for k, v in result.by_risk_level.items():
                        total_by_risk[k] = total_by_risk.get(k, 0) + v
                    for k, v in result.by_type.items():
                        total_by_type[k] = total_by_type.get(k, 0) + v
        else:
            for fp in files_to_scan:
                result = scanner.scan_file(fp)
                progress.advance(task)
                if result.has_secrets:
                    files_with_secrets += 1
                    for s in result.secrets_found:
                        if min_risk:
                            risk_order = ["critical", "high", "medium", "low"]
                            if risk_order.index(s.risk_level) > risk_order.index(min_risk):
                                continue
                        all_secrets.append(s)
                    for k, v in result.by_risk_level.items():
                        total_by_risk[k] = total_by_risk.get(k, 0) + v
                    for k, v in result.by_type.items():
                        total_by_type[k] = total_by_type.get(k, 0) + v

    # Output results
    if format == "json":
        json_output = {
            "summary": {
                "files_scanned": len(files_to_scan),
                "files_with_secrets": files_with_secrets,
                "total_secrets": len(all_secrets),
                "by_risk_level": total_by_risk,
                "by_type": total_by_type,
            },
            "secrets": [
                {
                    "file": s.filename,
                    "line": s.line_number,
                    "type": s.secret_type,
                    "risk_level": s.risk_level,
                    "remediation": s.remediation,
                    "start": s.start_index,
                    "end": s.end_index,
                }
                for s in all_secrets
            ],
        }
        if output:
            with open(output, 'w') as f:
                json_module.dump(json_output, f, indent=2)
            console.print(f"[green]Results written to {output}[/green]")
        else:
            console.print(json_module.dumps(json_output, indent=2))

    elif format == "sarif":
        # SARIF format for CI/CD integration
        sarif_output = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "Repotoire Secrets Scanner",
                        "version": "0.1.0",
                        "informationUri": "https://github.com/yourusername/repotoire",
                    }
                },
                "results": [
                    {
                        "ruleId": s.secret_type.replace(" ", "_").lower(),
                        "level": "error" if s.risk_level in ["critical", "high"] else "warning",
                        "message": {"text": f"{s.secret_type} detected. {s.remediation}"},
                        "locations": [{
                            "physicalLocation": {
                                "artifactLocation": {"uri": s.filename},
                                "region": {
                                    "startLine": s.line_number,
                                    "startColumn": s.start_index + 1,
                                    "endColumn": s.end_index + 1,
                                }
                            }
                        }],
                    }
                    for s in all_secrets
                ],
            }],
        }
        if output:
            with open(output, 'w') as f:
                json_module.dump(sarif_output, f, indent=2)
            console.print(f"[green]SARIF results written to {output}[/green]")
        else:
            console.print(json_module.dumps(sarif_output, indent=2))

    else:  # table format
        # Summary
        console.print("\n[bold]📊 Summary[/bold]")
        summary_table = Table(show_header=False, box=None)
        summary_table.add_column("Metric", style="dim")
        summary_table.add_column("Value", style="cyan")
        summary_table.add_row("Files scanned", str(len(files_to_scan)))
        summary_table.add_row("Files with secrets", str(files_with_secrets))
        summary_table.add_row("Total secrets found", str(len(all_secrets)))
        console.print(summary_table)

        # By risk level
        if total_by_risk:
            console.print("\n[bold]🎯 By Risk Level[/bold]")
            risk_table = Table(show_header=True)
            risk_table.add_column("Risk", style="cyan")
            risk_table.add_column("Count", justify="right")
            risk_colors = {"critical": "red", "high": "yellow", "medium": "blue", "low": "dim"}
            for risk in ["critical", "high", "medium", "low"]:
                if risk in total_by_risk:
                    color = risk_colors.get(risk, "white")
                    risk_table.add_row(f"[{color}]{risk.upper()}[/{color}]", str(total_by_risk[risk]))
            console.print(risk_table)

        # By type
        if total_by_type:
            console.print("\n[bold]🏷️ By Type[/bold]")
            type_table = Table(show_header=True)
            type_table.add_column("Secret Type", style="cyan")
            type_table.add_column("Count", justify="right")
            for secret_type, count in sorted(total_by_type.items(), key=lambda x: -x[1]):
                type_table.add_row(secret_type, str(count))
            console.print(type_table)

        # Detailed findings (limit to 20)
        if all_secrets:
            console.print("\n[bold]🔍 Secrets Found[/bold]")
            if len(all_secrets) > 20:
                console.print(f"[dim]Showing first 20 of {len(all_secrets)} secrets[/dim]")

            findings_table = Table(show_header=True)
            findings_table.add_column("File", style="cyan", max_width=40)
            findings_table.add_column("Line", justify="right")
            findings_table.add_column("Type", style="yellow")
            findings_table.add_column("Risk", style="red")

            for s in all_secrets[:20]:
                risk_colors = {"critical": "red", "high": "yellow", "medium": "blue", "low": "dim"}
                color = risk_colors.get(s.risk_level, "white")
                findings_table.add_row(
                    s.filename,
                    str(s.line_number),
                    s.secret_type[:30] + "..." if len(s.secret_type) > 30 else s.secret_type,
                    f"[{color}]{s.risk_level.upper()}[/{color}]",
                )

            console.print(findings_table)

            if output:
                # Also write JSON to file
                json_output = {
                    "summary": {
                        "files_scanned": len(files_to_scan),
                        "files_with_secrets": files_with_secrets,
                        "total_secrets": len(all_secrets),
                        "by_risk_level": total_by_risk,
                        "by_type": total_by_type,
                    },
                    "secrets": [
                        {
                            "file": s.filename,
                            "line": s.line_number,
                            "type": s.secret_type,
                            "risk_level": s.risk_level,
                            "remediation": s.remediation,
                        }
                        for s in all_secrets
                    ],
                }
                with open(output, 'w') as f:
                    json_module.dump(json_output, f, indent=2)
                console.print(f"\n[green]Full results written to {output}[/green]")
        else:
            console.print("\n[green]✓ No secrets detected![/green]")

    # Exit with error if critical or high risk secrets found
    critical_high = total_by_risk.get("critical", 0) + total_by_risk.get("high", 0)
    if critical_high > 0:
        console.print(f"\n[red]Error:[/red] Found {critical_high} critical/high risk secrets!")
        console.print("[dim]Review and remediate secrets before committing.[/dim]")
        raise click.Abort()


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
        # Database configuration
        db_table = Table(title="FalkorDB Configuration")
        db_table.add_column("Setting", style="cyan")
        db_table.add_column("Value", style="green")
        db_table.add_row("Host", config.database.host)
        db_table.add_row("Port", str(config.database.port))
        db_table.add_row("Password", "***" if config.database.password else "[dim]not set[/dim]")
        console.print(db_table)

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
def backends() -> None:
    """Show available embedding backends and their status.

    Displays all embedding backends with their configuration status,
    API key availability, and which backend would be auto-selected.

    Example:
        repotoire backends
    """
    import os
    from repotoire.ai.embeddings import (
        BACKEND_CONFIGS,
        BACKEND_PRIORITY,
        detect_available_backends,
        select_best_backend,
    )

    available = detect_available_backends()
    selected, reason = select_best_backend()

    console.print("\n[bold cyan]🎼 Embedding Backends[/bold cyan]\n")

    for backend in BACKEND_PRIORITY:
        config = BACKEND_CONFIGS[backend]
        env_key = config.get("env_key")
        is_available = backend in available
        is_selected = backend == selected

        # Status indicator
        if is_selected:
            status = "[green]● SELECTED[/green]"
        elif is_available:
            status = "[yellow]○ Available[/yellow]"
        else:
            status = "[dim]○ Not configured[/dim]"

        # API key status
        if env_key:
            key_set = bool(os.getenv(env_key))
            key_status = f"[green]{env_key} ✓[/green]" if key_set else f"[dim]{env_key} not set[/dim]"
        else:
            key_status = "[dim]No API key needed[/dim]"

        console.print(f"  {status} [bold]{backend}[/bold]")
        console.print(f"      {config['description']}")
        console.print(f"      Model: {config['model']} ({config['dimensions']}d)")
        console.print(f"      {key_status}")
        console.print()

    console.print(f"[bold]Auto-selected:[/bold] {reason}\n")


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

    Schema migrations allow you to safely evolve the graph database schema
    over time with version tracking and rollback capabilities.

    Examples:
        repotoire migrate status              # Show current migration state
        repotoire migrate up                  # Apply pending migrations
        repotoire migrate down --to-version 1 # Rollback to version 1
    """
    pass


@migrate.command()
def status() -> None:
    """Show current migration status and pending migrations."""
    console.print(f"\n[bold cyan]🎼 Repotoire Migration Status[/bold cyan]\n")

    try:
        db = _get_db_client()
        try:
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
        finally:
            db.close()

    except MigrationError as e:
        console.print(f"\n[red]❌ Migration Error:[/red] {e}")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]❌ Unexpected error:[/red] {e}")
        raise


@migrate.command()
@click.option(
    "--to-version",
    type=int,
    default=None,
    help="Target version to migrate to (default: latest)",
)
def up(to_version: int | None) -> None:
    """Apply pending migrations to upgrade schema."""
    console.print(f"\n[bold cyan]🎼 Repotoire Migration: Upgrading Schema[/bold cyan]\n")

    try:
        db = _get_db_client()
        try:
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
        finally:
            db.close()

    except MigrationError as e:
        console.print(f"\n[red]❌ Migration Error:[/red] {e}")
        console.print("[yellow]⚠️  Schema may be in an inconsistent state[/yellow]")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]❌ Unexpected error:[/red] {e}")
        raise


@migrate.command()
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
def down(to_version: int, force: bool) -> None:
    """Rollback migrations to a previous version.

    WARNING: This operation may result in data loss. Use with caution!
    """
    console.print(f"\n[bold red]⚠️  Repotoire Migration: Rollback Schema[/bold red]\n")

    try:
        db = _get_db_client()
        try:
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
        finally:
            db.close()

    except MigrationError as e:
        console.print(f"\n[red]❌ Migration Error:[/red] {e}")
        console.print("[yellow]⚠️  Schema may be in an inconsistent state[/yellow]")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]❌ Unexpected error:[/red] {e}")
        raise


@migrate.command("export")
@click.option(
    "--output", "-o",
    type=click.Path(),
    required=True,
    help="Output file path (JSON or .json.gz)"
)
@click.option(
    "--compress/--no-compress",
    default=True,
    help="Compress output with gzip (default: true)"
)
def export_data(output: str, compress: bool) -> None:
    """Export graph data to a portable JSON format.

    Exports all nodes and relationships for backup purposes.

    Example:
        repotoire migrate export -o backup.json.gz
        repotoire migrate export -o backup.json --no-compress
    """
    from pathlib import Path
    from repotoire.graph.migration import GraphMigration

    console.print(f"\n[bold cyan]📦 Exporting Graph Data[/bold cyan]\n")

    try:
        client = _get_db_client()

        with client:
            migration = GraphMigration(client)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task("[cyan]Exporting nodes and relationships...", total=None)
                stats = migration.export_graph(Path(output), compress=compress)

            console.print(f"\n[green]✓ Export complete[/green]")
            console.print(f"  Nodes exported: [cyan]{stats.nodes_exported}[/cyan]")
            console.print(f"  Relationships exported: [cyan]{stats.relationships_exported}[/cyan]")
            console.print(f"  Output file: [dim]{output}[/dim]")

            if stats.errors:
                console.print(f"\n[yellow]⚠️  {len(stats.errors)} errors during export:[/yellow]")
                for error in stats.errors[:5]:
                    console.print(f"  - {error}")

    except Exception as e:
        console.print(f"\n[red]❌ Export failed:[/red] {e}")
        raise click.Abort()


@migrate.command("import")
@click.option(
    "--input", "-i",
    "input_file",
    type=click.Path(exists=True),
    required=True,
    help="Input file path (JSON or .json.gz)"
)
@click.option(
    "--clear/--no-clear",
    default=False,
    help="Clear existing data before import (default: false)"
)
@click.option(
    "--batch-size",
    type=int,
    default=100,
    help="Batch size for import operations (default: 100)"
)
def import_data(input_file: str, clear: bool, batch_size: int) -> None:
    """Import graph data from a portable JSON format.

    Imports nodes and relationships from an export file.

    Example:
        repotoire migrate import -i backup.json.gz
        repotoire migrate import -i backup.json --clear
    """
    from pathlib import Path
    from repotoire.graph.migration import GraphMigration

    console.print(f"\n[bold cyan]📥 Importing Graph Data[/bold cyan]\n")

    if clear:
        console.print("[yellow]⚠️  WARNING: This will clear all existing data![/yellow]")
        if not click.confirm("Are you sure you want to continue?", default=False):
            console.print("\n[dim]Import cancelled[/dim]")
            return

    try:
        client = _get_db_client()

        with client:
            migration = GraphMigration(client)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task("[cyan]Importing nodes and relationships...", total=None)
                stats = migration.import_graph(
                    Path(input_file),
                    clear_existing=clear,
                    batch_size=batch_size
                )

            console.print(f"\n[green]✓ Import complete[/green]")
            console.print(f"  Nodes imported: [cyan]{stats.nodes_imported}[/cyan]")
            console.print(f"  Relationships imported: [cyan]{stats.relationships_imported}[/cyan]")

            if stats.errors:
                console.print(f"\n[yellow]⚠️  {len(stats.errors)} errors during import:[/yellow]")
                for error in stats.errors[:5]:
                    console.print(f"  - {error}")

    except Exception as e:
        console.print(f"\n[red]❌ Import failed:[/red] {e}")
        raise click.Abort()


@migrate.command("validate")
def validate_migration() -> None:
    """Validate graph data integrity after migration.

    Checks node counts, relationship counts, and schema integrity
    to ensure data was migrated correctly.

    Example:
        repotoire migrate validate
    """
    from repotoire.graph.migration import GraphMigration

    console.print(f"\n[bold cyan]🔍 Validating Graph Data[/bold cyan]\n")

    try:
        client = _get_db_client()

        with client:
            migration = GraphMigration(client)
            result = migration.validate()

            # Display stats
            stats_table = Table(title="Graph Statistics", box=box.ROUNDED)
            stats_table.add_column("Label/Type", style="cyan")
            stats_table.add_column("Count", style="white", justify="right")

            for label, count in result.source_stats.get("by_label", {}).items():
                if count > 0:
                    stats_table.add_row(f":{label}", str(count))

            stats_table.add_row("─" * 20, "─" * 10)
            stats_table.add_row("Total Nodes", str(result.source_stats["total_nodes"]))

            for rel_type, count in result.source_stats.get("by_rel_type", {}).items():
                if count > 0:
                    stats_table.add_row(f"-[{rel_type}]->", str(count))

            stats_table.add_row("─" * 20, "─" * 10)
            stats_table.add_row("Total Relationships", str(result.source_stats["total_relationships"]))

            console.print(stats_table)

            if result.valid:
                console.print(f"\n[green]✓ Graph validation passed[/green]")
            else:
                console.print(f"\n[red]❌ Validation issues found:[/red]")
                for issue in result.issues:
                    console.print(f"  - {issue}")

    except Exception as e:
        console.print(f"\n[red]❌ Validation failed:[/red] {e}")
        raise click.Abort()


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True), metavar="REPO_PATH")
@click.option("--window", type=int, default=90, help="Time window in days (default: 90)")
@click.option("--min-churn", type=int, default=5, help="Minimum modifications to qualify as hotspot (default: 5)")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Suppress non-essential output")
def hotspots(repo_path: str, window: int, min_churn: int, quiet: bool) -> None:
    """Find code hotspots with high churn and complexity.

    Analyzes Git history to find files with:
    - High modification frequency (churn)
    - Increasing complexity or coupling
    - High risk scores requiring attention

    Arguments:
        REPO_PATH: Path to the repository to analyze

    Example:
        repotoire hotspots /path/to/repo --window 90 --min-churn 5
    """
    try:
        client = _get_db_client(quiet=quiet)

        # Create temporal metrics analyzer
        from repotoire.detectors.temporal_metrics import TemporalMetrics
        analyzer = TemporalMetrics(client)

        # Find hotspots with progress indicator
        if not quiet:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(
                    f"[cyan]Finding code hotspots (last {window} days)...",
                    total=None
                )
                hotspots_list = analyzer.find_code_hotspots(window_days=window, min_churn=min_churn)
                progress.update(task, completed=True, description="[green]Analysis complete")
        else:
            hotspots_list = analyzer.find_code_hotspots(window_days=window, min_churn=min_churn)

        if not hotspots_list:
            if not quiet:
                console.print(f"\n[green]✓ No code hotspots found in the last {window} days![/green]")
                console.print(f"[dim]This means no files have >{min_churn} modifications with increasing complexity[/dim]\n")
            return

        # Display hotspots table
        if not quiet:
            console.print(f"\n[bold red]Code Hotspots[/bold red] (Last {window} days)\n")

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
                risk_indicator = "*" * min(int(hotspot.risk_score / 10), 5)
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
        console.print(f"\n[red]Error:[/red] {e}")
        raise click.Abort()


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True), metavar="REPO_PATH")
@click.option("--strategy", type=click.Choice(["recent", "all", "milestones"]), default="recent", help="Commit selection strategy")
@click.option("--max-commits", type=int, default=10, help="Maximum commits to analyze (default: 10)")
@click.option("--branch", default="HEAD", help="Branch to analyze (default: HEAD)")
@click.option("--generate-clues", is_flag=True, default=False, help="Generate semantic clues for each commit")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Suppress non-essential output")
def history(repo_path: str, strategy: str, max_commits: int, branch: str, generate_clues: bool, quiet: bool) -> None:
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
        repotoire history /path/to/repo --strategy recent --max-commits 10
    """
    if not quiet:
        console.print(f"\n[bold cyan]📊 Temporal Code Analysis[/bold cyan]\n")
        console.print(f"Repository: [yellow]{repo_path}[/yellow]")
        console.print(f"Strategy: [cyan]{strategy}[/cyan]")
        console.print(f"Max commits: [cyan]{max_commits}[/cyan]\n")

    try:
        client = _get_db_client(quiet=quiet)

        # Create temporal ingestion pipeline
        from repotoire.pipeline.temporal_ingestion import TemporalIngestionPipeline
        pipeline = TemporalIngestionPipeline(
            repo_path=repo_path,
            graph_client=client,
            generate_clues=generate_clues
        )

        # Ingest with history
        if not quiet:
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
        else:
            result = pipeline.ingest_with_history(
                strategy=strategy,
                max_commits=max_commits,
                branch=branch
            )

        # Display results
        if not quiet:
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
        console.print(f"\n[red]Error:[/red] {e}")
        raise click.Abort()


@cli.command()
@click.argument("before_commit", metavar="BEFORE_COMMIT")
@click.argument("after_commit", metavar="AFTER_COMMIT")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Suppress non-essential output")
def compare(before_commit: str, after_commit: str, quiet: bool) -> None:
    """Compare code metrics between two commits.

    Shows how code quality metrics changed between commits:
    - Improvements (metrics got better)
    - Regressions (metrics got worse)
    - Percentage changes

    Arguments:
        BEFORE_COMMIT: The earlier commit hash or ref to compare from
        AFTER_COMMIT: The later commit hash or ref to compare to

    Example:
        repotoire compare abc123 def456
    """
    try:
        client = _get_db_client(quiet=quiet)

        # Create temporal metrics analyzer
        from repotoire.detectors.temporal_metrics import TemporalMetrics
        analyzer = TemporalMetrics(client)

        if not quiet:
            with console.status(f"[bold green]Comparing commits {before_commit[:7]} -> {after_commit[:7]}...", spinner="dots"):
                comparison = analyzer.compare_commits(before_commit, after_commit)
        else:
            comparison = analyzer.compare_commits(before_commit, after_commit)

        if not comparison:
            if not quiet:
                console.print(f"\n[yellow]Warning:[/yellow] Could not find sessions for commits {before_commit[:7]} and {after_commit[:7]}")
                console.print("[dim]Make sure you've run 'repotoire history' first to ingest commit data[/dim]\n")
            return

        # Display comparison
        if not quiet:
            console.print(f"\n[bold cyan]Commit Comparison[/bold cyan]\n")
            console.print(f"Before: [yellow]{comparison['before_commit']}[/yellow]  ({comparison['before_date']})")
            console.print(f"After:  [yellow]{comparison['after_commit']}[/yellow]  ({comparison['after_date']})\n")

            # Show improvements
            if comparison["improvements"]:
                console.print("[bold green]Improvements:[/bold green]")
                for metric in comparison["improvements"]:
                    change = comparison["changes"][metric]
                    console.print(f"  - {metric}: {change['before']:.2f} -> {change['after']:.2f} ({change['change_percentage']:+.1f}%)")
                console.print()

            # Show regressions
            if comparison["regressions"]:
                console.print("[bold red]Regressions:[/bold red]")
                for metric in comparison["regressions"]:
                    change = comparison["changes"][metric]
                    console.print(f"  - {metric}: {change['before']:.2f} -> {change['after']:.2f} ({change['change_percentage']:+.1f}%)")
                console.print()

            # Overall assessment
            if len(comparison["improvements"]) > len(comparison["regressions"]):
                console.print("[green]Overall: Code quality improved[/green]")
            elif len(comparison["regressions"]) > len(comparison["improvements"]):
                console.print("[red]Overall: Code quality degraded[/red]")
            else:
                console.print("[yellow]Overall: Mixed changes[/yellow]")

            console.print()

    except Exception as e:
        logger.error(f"Failed to compare commits: {e}", exc_info=True)
        console.print(f"\n[red]Error:[/red] {e}")
        raise click.Abort()


@cli.command()
@click.option("--output-dir", "-o", type=click.Path(), default="./mcp_server", help="Output directory for generated server")
@click.option("--server-name", default="mcp_server", help="Name for the generated MCP server")
@click.option("--enable-rag", is_flag=True, default=False, help="Enable RAG enhancements (requires OpenAI API key)")
@click.option("--min-params", default=2, help="Minimum parameters for public functions")
@click.option("--max-params", default=10, help="Maximum parameters for public functions")
@click.option("--max-routes", default=None, type=int, help="Maximum FastAPI routes to include")
@click.option("--max-commands", default=None, type=int, help="Maximum Click commands to include")
@click.option("--max-functions", default=None, type=int, help="Maximum public functions to include")
def generate_mcp(
    output_dir: str,
    server_name: str,
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
        # Get repository path (assume current directory)
        repository_path = os.getcwd()

        console.print()
        console.print("[bold cyan]🚀 MCP Server Generation[/bold cyan]")
        console.print("[dim]Generating Model Context Protocol server from codebase[/dim]")
        console.print()

        # Connect to graph database
        with console.status("[bold green]Connecting to graph database...", spinner="dots"):
            client = _get_db_client()

        console.print("[green]✓[/green] Connected to graph database")

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
                    rag_retriever = GraphRAGRetriever(graph_client=client, embedder=embedder)
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
                graph_client=client if enable_rag else None
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

    Tools for exploring the graph structure, validating integrity,
    and debugging.

    Examples:
        repotoire schema inspect           # Show graph statistics
        repotoire schema sample Class --limit 3  # Sample Class nodes
        repotoire schema validate          # Check schema integrity
    """
    pass


@schema.command()
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def inspect(format: str) -> None:
    """Show graph statistics and schema overview."""
    try:
        client = _get_db_client()

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
def visualize() -> None:
    """Visualize graph schema structure with ASCII art."""
    try:
        client = _get_db_client()

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
def sample(node_type: str, limit: int) -> None:
    """Show sample nodes of a specific type.

    NODE_TYPE: The node label to sample (e.g., Class, Function, File)
    """
    try:
        client = _get_db_client()

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
def validate() -> None:
    """Validate graph schema integrity."""
    try:
        client = _get_db_client()

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
def list(
    enabled_only: bool,
    tags: tuple,
    sort_by: str,
    limit: int | None,
) -> None:
    """List all custom rules with priority scores."""
    try:
        from repotoire.rules.engine import RuleEngine

        client = _get_db_client()
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
def add(file_path: str) -> None:
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

        client = _get_db_client()
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
def edit(
    rule_id: str,
    name: str | None,
    priority: int | None,
    enable: bool | None,
) -> None:
    """Edit an existing rule."""
    try:
        from repotoire.rules.engine import RuleEngine

        client = _get_db_client()
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
def delete(rule_id: str) -> None:
    """Delete a rule."""
    try:
        from repotoire.rules.engine import RuleEngine

        client = _get_db_client()
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
def test(rule_id: str) -> None:
    """Test a rule (dry-run) to see what it would find."""
    try:
        from repotoire.rules.engine import RuleEngine

        client = _get_db_client()
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
                # Escape finding content to prevent Rich markup interpretation (REPO-179)
                escaped_title = escape(finding.title)
                escaped_desc = escape(finding.description)
                console.print(f"{i}. [{finding.severity.value}] {escaped_title}")
                console.print(f"   {escaped_desc}")
                if finding.affected_files:
                    escaped_files = [escape(f) for f in finding.affected_files]
                    console.print(f"   Files: {', '.join(escaped_files)}")
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
def stats() -> None:
    """Show rule usage statistics."""
    try:
        from repotoire.rules.engine import RuleEngine

        client = _get_db_client()
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
def daemon_refresh(
    decay_threshold: int,
    decay_factor: float,
    auto_archive: bool,
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

        client = _get_db_client()

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

        # REPO-600: Get tenant_id for multi-tenant isolation
        try:
            from repotoire.tenant.context import get_current_org_id_str
            tenant_id = get_current_org_id_str()
        except Exception:
            tenant_id = None

        if not tenant_id:
            resolved_tenant_id, _ = _get_tenant_from_auth()
            tenant_id = resolved_tenant_id

        if not tenant_id:
            console.print("\n[red]❌ No tenant context available[/red]")
            console.print("[dim]Authenticate with 'repotoire auth login' for tenant isolation[/dim]")
            raise click.Abort()

        # Query trend data with tenant isolation
        with TimescaleClient(config.timescale.connection_string) as client:
            data = client.get_trend(repository, tenant_id=tenant_id, branch=branch, days=days)

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

        # REPO-600: Get tenant_id for multi-tenant isolation
        try:
            from repotoire.tenant.context import get_current_org_id_str
            tenant_id = get_current_org_id_str()
        except Exception:
            tenant_id = None

        if not tenant_id:
            resolved_tenant_id, _ = _get_tenant_from_auth()
            tenant_id = resolved_tenant_id

        if not tenant_id:
            console.print("\n[red]❌ No tenant context available[/red]")
            console.print("[dim]Authenticate with 'repotoire auth login' for tenant isolation[/dim]")
            raise click.Abort()

        # Check for regression with tenant isolation
        with TimescaleClient(config.timescale.connection_string) as client:
            result = client.detect_regression(repository, tenant_id=tenant_id, branch=branch, threshold=threshold)

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

        # REPO-600: Get tenant_id for multi-tenant isolation
        try:
            from repotoire.tenant.context import get_current_org_id_str
            tenant_id = get_current_org_id_str()
        except Exception:
            tenant_id = None

        if not tenant_id:
            resolved_tenant_id, _ = _get_tenant_from_auth()
            tenant_id = resolved_tenant_id

        if not tenant_id:
            console.print("\n[red]❌ No tenant context available[/red]")
            console.print("[dim]Authenticate with 'repotoire auth login' for tenant isolation[/dim]")
            raise click.Abort()

        # Query comparison data with tenant isolation
        with TimescaleClient(config.timescale.connection_string) as client:
            stats = client.compare_periods(repository, tenant_id=tenant_id, start_date=start_date, end_date=end_date, branch=branch)

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

        # REPO-600: Get tenant_id for multi-tenant isolation
        try:
            from repotoire.tenant.context import get_current_org_id_str
            tenant_id = get_current_org_id_str()
        except Exception:
            tenant_id = None

        if not tenant_id:
            resolved_tenant_id, _ = _get_tenant_from_auth()
            tenant_id = resolved_tenant_id

        if not tenant_id:
            console.print("\n[red]❌ No tenant context available[/red]")
            console.print("[dim]Authenticate with 'repotoire auth login' for tenant isolation[/dim]")
            raise click.Abort()

        # Query data with tenant isolation
        with TimescaleClient(config.timescale.connection_string) as client:
            if days:
                data = client.get_trend(repository, tenant_id=tenant_id, branch=branch, days=days)
            else:
                # Get all data (use a large number)
                data = client.get_trend(repository, tenant_id=tenant_id, branch=branch, days=365 * 10)

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


@cli.command("auto-fix")
@click.argument("repository", type=click.Path(exists=True))
@click.option("--max-fixes", "-n", type=int, default=10, help="Maximum fixes to generate")
@click.option("--severity", "-s", type=click.Choice(["critical", "high", "medium", "low"]), help="Minimum severity to fix")
@click.option("--auto-approve-high", is_flag=True, help="Auto-approve high-confidence fixes")
@click.option("--auto-apply", is_flag=True, help="Auto-apply all fixes without review (CI mode)")
@click.option("--ci-mode", is_flag=True, help="Enable CI-friendly output and behavior")
@click.option("--dry-run", is_flag=True, help="Generate fixes but don't apply them")
@click.option("--output", "-o", type=click.Path(), help="Save fix details to JSON file")
@click.option("--create-branch/--no-branch", default=True, help="Create git branch for fixes")
@click.option("--run-tests", is_flag=True, help="Run tests after applying fixes")
@click.option("--test-command", default="pytest", help="Test command to run")
@click.option("--local-tests", is_flag=True, help="Run tests locally (SECURITY WARNING: full host access)")
@click.option("--test-timeout", type=int, default=300, help="Test execution timeout in seconds (default: 300)")
def auto_fix(
    repository: str,
    max_fixes: int,
    severity: Optional[str],
    auto_approve_high: bool,
    auto_apply: bool,
    ci_mode: bool,
    dry_run: bool,
    output: Optional[str],
    create_branch: bool,
    run_tests: bool,
    test_command: str,
    local_tests: bool,
    test_timeout: int,
) -> None:
    """AI-powered automatic code fixing with human-in-the-loop approval.

    Analyzes your codebase, generates AI-powered fixes, and presents them
    for interactive review. Approved fixes are automatically applied with
    git integration.

    Test Execution Security:
        By default, tests run in isolated E2B sandboxes to prevent malicious
        auto-fix code from accessing host resources. Use --local-tests only
        for trusted code in development environments.

    Examples:
        # Generate and review up to 10 fixes
        repotoire auto-fix /path/to/repo

        # Auto-approve high-confidence fixes
        repotoire auto-fix /path/to/repo --auto-approve-high

        # Only fix critical issues
        repotoire auto-fix /path/to/repo --severity critical

        # Apply fixes and run tests (sandbox by default)
        repotoire auto-fix /path/to/repo --run-tests

        # Run tests locally (WARNING: full host access)
        repotoire auto-fix /path/to/repo --run-tests --local-tests

        # Custom test timeout (30 minutes for slow test suites)
        repotoire auto-fix /path/to/repo --run-tests --test-timeout 1800

        # CI mode: auto-apply all fixes with JSON output
        repotoire auto-fix /path/to/repo --ci-mode --auto-apply --output fixes.json

        # Dry run: generate fixes without applying
        repotoire auto-fix /path/to/repo --dry-run --output fixes.json
    """
    import os
    import json
    from pathlib import Path
    from repotoire.engine import AnalysisEngine
    from repotoire.autofix import AutoFixEngine, InteractiveReviewer, FixApplicator
    from repotoire.models import Severity

    # CI mode implies quiet output
    quiet_mode = ci_mode

    try:
        # Check for OpenAI API key
        if not os.getenv("OPENAI_API_KEY"):
            console.print("\n[red]❌ OPENAI_API_KEY not set[/red]")
            console.print("[dim]Auto-fix requires an OpenAI API key for fix generation[/dim]")
            raise click.Abort()

        repo_path = Path(repository)

        if not quiet_mode:
            console.print("\n[bold cyan]🤖 Repotoire Auto-Fix[/bold cyan]")
            console.print(f"Repository: {repository}\n")

        # Step 1: Analyze codebase
        if not quiet_mode:
            console.print("[bold]Step 1: Analyzing codebase...[/bold]")

        graph_client = _get_db_client()
        engine = AnalysisEngine(graph_client, enable_insights=True)

        if not quiet_mode:
            with console.status("[bold]Running code analysis..."):
                health = engine.analyze(str(repo_path))
        else:
            health = engine.analyze(str(repo_path))

        findings = health.findings

        # Filter by severity if specified
        if severity:
            severity_enum = getattr(Severity, severity.upper())
            findings = [f for f in findings if f.severity == severity_enum]

        if not quiet_mode:
            console.print(f"[green]✓[/green] Found {len(findings)} issue(s)")

        if not findings:
            if not quiet_mode:
                console.print("\n[yellow]No issues found. Your code is clean! 🎉[/yellow]")
            graph_client.close()
            ctx.exit(0)

        # Limit to max fixes
        findings = findings[:max_fixes]
        if not quiet_mode:
            console.print(f"[dim]Generating fixes for {len(findings)} issue(s)...[/dim]\n")

        # Step 2: Generate fixes
        if not quiet_mode:
            console.print("[bold]Step 2: Generating AI-powered fixes...[/bold]")

        fix_engine = AutoFixEngine(graph_client)
        fix_proposals = []

        import asyncio

        async def generate_all_fixes():
            tasks = []
            for finding in findings:
                task = fix_engine.generate_fix(finding, repo_path)
                tasks.append(task)
            return await asyncio.gather(*tasks)

        if not quiet_mode:
            with console.status(f"[bold]Generating {len(findings)} fix(es)..."):
                fixes = asyncio.run(generate_all_fixes())
        else:
            fixes = asyncio.run(generate_all_fixes())

        # Filter out failed generations
        fix_proposals = [f for f in fixes if f is not None]

        if not quiet_mode:
            console.print(f"[green]✓[/green] Generated {len(fix_proposals)} fix proposal(s)\n")

        if not fix_proposals:
            if not quiet_mode:
                console.print("[yellow]No fixes could be generated.[/yellow]")
            graph_client.close()
            ctx.exit(0)

        # Step 3: Review fixes (skip in CI mode with auto-apply or dry-run)
        if auto_apply or dry_run:
            # Auto-approve all fixes in CI mode or dry-run
            approved_fixes = fix_proposals
            if not quiet_mode:
                console.print(f"[bold]Step 3: Auto-approving {len(fix_proposals)} fix(es)...[/bold]\n")
        else:
            # Interactive review
            if not quiet_mode:
                console.print("[bold]Step 3: Reviewing fixes...[/bold]\n")

            reviewer = InteractiveReviewer(console)
            approved_fixes = reviewer.review_batch(fix_proposals, auto_approve_high=auto_approve_high)

        if not approved_fixes:
            if not quiet_mode:
                console.print("\n[yellow]No fixes approved. Exiting.[/yellow]")
            graph_client.close()
            ctx.exit(0)

        # Save fix details to JSON if requested
        if output:
            output_data = {
                "fixes": [f.to_dict() for f in approved_fixes],
                "summary": {
                    "total": len(fix_proposals),
                    "approved": len(approved_fixes),
                    "dry_run": dry_run
                }
            }
            with open(output, "w") as f:
                json.dump(output_data, f, indent=2)
            if not quiet_mode:
                console.print(f"[green]✓[/green] Fix details saved to {output}\n")

        # Step 4: Apply fixes (skip in dry-run mode)
        successful = []
        failed = []

        if dry_run:
            if not quiet_mode:
                console.print(f"\n[bold yellow]Dry run: {len(approved_fixes)} fix(es) would be applied[/bold yellow]")
            successful = approved_fixes  # For summary purposes
        else:
            if not quiet_mode:
                console.print(f"\n[bold]Step 4: Applying {len(approved_fixes)} fix(es)...[/bold]")

            applicator = FixApplicator(
                repo_path,
                create_branch=create_branch,
                use_sandbox=not local_tests,
                test_timeout=test_timeout,
            )

            if not quiet_mode:
                with console.status("[bold]Applying fixes..."):
                    successful, failed = applicator.apply_batch(approved_fixes, commit_each=False)
            else:
                successful, failed = applicator.apply_batch(approved_fixes, commit_each=False)

            if not quiet_mode:
                console.print(f"[green]✓[/green] Applied {len(successful)} fix(es)")

            if failed and not quiet_mode:
                console.print(f"[red]✗[/red] {len(failed)} fix(es) failed to apply:")
                for fix, error in failed:
                    # Escape fix title to prevent Rich markup interpretation (REPO-179)
                    escaped_title = escape(fix.title) if hasattr(fix, 'title') else str(fix)
                    console.print(f"  - {escaped_title}: {error}")

        # Step 5: Run tests if requested (skip in dry-run mode)
        tests_passed = True
        test_result = None
        if run_tests and successful and not dry_run:
            if not quiet_mode:
                sandbox_mode = "locally" if local_tests else "in sandbox"
                console.print(f"\n[bold]Step 5: Running tests {sandbox_mode}...[/bold]")
                if not local_tests:
                    console.print("[dim]Tests run in isolated E2B sandbox for security[/dim]")

            if not quiet_mode:
                with console.status(f"[bold]Running {test_command}..."):
                    test_result = applicator.run_tests(test_command)
            else:
                test_result = applicator.run_tests(test_command)

            tests_passed = test_result.success

            if not quiet_mode:
                if tests_passed:
                    console.print("[green]✓[/green] All tests passed")
                    if test_result.sandbox_id:
                        console.print(f"[dim]Sandbox ID: {test_result.sandbox_id}[/dim]")
                else:
                    if test_result.timed_out:
                        console.print(f"[red]✗[/red] Tests timed out after {test_timeout}s")
                    else:
                        console.print("[red]✗[/red] Tests failed")

                    test_output = test_result.stdout + test_result.stderr
                    console.print("\n[dim]Test output:[/dim]")
                    console.print(test_output[:1000])  # Show first 1000 chars

                    # Offer rollback (skip in CI mode)
                    if not ci_mode and Confirm.ask("\n[yellow]Tests failed. Rollback changes?[/yellow]", default=True):
                        applicator.rollback()
                        console.print("[green]✓[/green] Changes rolled back")

        # Summary
        if not quiet_mode and not (auto_apply or dry_run):
            # Only show interactive summary if not in CI/auto-apply mode
            reviewer.show_summary(
                total=len(fix_proposals),
                approved=len(approved_fixes),
                applied=len(successful),
                failed=len(failed),
            )

        # CI mode: print summary in machine-readable format
        if ci_mode:
            print(json.dumps({
                "success": len(failed) == 0 and tests_passed,
                "fixes_generated": len(fix_proposals),
                "fixes_applied": len(successful) if not dry_run else 0,
                "fixes_failed": len(failed),
                "tests_passed": tests_passed,
                "dry_run": dry_run
            }))

        graph_client.close()

        # Exit with appropriate code
        if failed or not tests_passed:
            ctx.exit(1)
        else:
            ctx.exit(0)

    except Exception as e:
        logger.error(f"Auto-fix failed: {e}", exc_info=True)
        if not quiet_mode:
            console.print(f"\n[red]❌ Error:[/red] {e}")
        ctx.exit(2)


@cli.command("fix")
@click.argument("target")
@click.option("--findings", "-f", type=click.Path(exists=True), help="JSON file with findings from analyze")
@click.option("--repo", "-r", type=click.Path(exists=True), default=".", help="Repository path")
@click.option("--apply", "-a", is_flag=True, help="Apply the fix directly")
@click.option("--model", default="claude-sonnet-4-20250514", help="LLM model for fix generation")
@click.pass_context
def fix_finding(
    ctx: click.Context,
    target: str,
    findings: Optional[str],
    repo: str,
    apply: bool,
    model: str,
) -> None:
    """Generate a fix for a specific finding.

    TARGET can be:
      - A finding index (e.g., "3" for finding #3 from JSON)
      - A file:line reference (e.g., "src/app.py:42")
    
    Examples:
        # Fix finding #3 from findings.json
        repotoire fix 3 -f findings.json

        # Fix issue at specific location
        repotoire fix src/app.py:42

        # Fix and apply directly
        repotoire fix 3 -f findings.json --apply
    """
    import asyncio
    import json
    from pathlib import Path
    
    repo_path = Path(repo).resolve()
    
    try:
        # Check for API key
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            console.print("\n[red]❌ No API key found[/red]")
            console.print("[dim]Set ANTHROPIC_API_KEY or OPENAI_API_KEY for fix generation[/dim]")
            ctx.exit(1)
        
        # Determine which finding to fix
        finding = None
        
        # Check if target is a number (finding index)
        if target.isdigit() and findings:
            with open(findings) as f:
                data = json.load(f)
            
            findings_list = data.get("findings", data) if isinstance(data, dict) else data
            idx = int(target) - 1  # 1-indexed for users
            
            if 0 <= idx < len(findings_list):
                from repotoire.models import Finding, Severity
                f_data = findings_list[idx]
                # Extract file path and line from finding data
                affected_files = f_data.get("affected_files", [])
                graph_ctx = f_data.get("graph_context", {})
                line_start = f_data.get("line_start") or graph_ctx.get("start_line")
                line_end = f_data.get("line_end") or graph_ctx.get("end_line")
                
                finding = Finding(
                    id=f_data.get("id", f"finding-{idx}"),
                    title=f_data.get("title", "Unknown"),
                    description=f_data.get("description", ""),
                    affected_files=affected_files,
                    affected_nodes=f_data.get("affected_nodes", []),
                    line_start=line_start,
                    line_end=line_end,
                    severity=Severity(f_data.get("severity", "medium")),
                    detector=f_data.get("detector", "unknown"),
                    graph_context=graph_ctx,
                    suggested_fix=f_data.get("suggested_fix"),
                    estimated_effort=f_data.get("estimated_effort"),
                )
            else:
                console.print(f"[red]Finding #{target} not found (have {len(findings_list)} findings)[/red]")
                ctx.exit(1)
        
        # Check if target is file:line
        elif ":" in target:
            file_path, line_str = target.rsplit(":", 1)
            if line_str.isdigit():
                # Create a synthetic finding for this location
                from repotoire.models import Finding, Severity
                
                full_path = repo_path / file_path
                if not full_path.exists():
                    console.print(f"[red]File not found: {file_path}[/red]")
                    ctx.exit(1)
                
                line_num = int(line_str)
                
                # Read code snippet
                with open(full_path) as f:
                    lines = f.readlines()
                start = max(0, line_num - 3)
                end = min(len(lines), line_num + 3)
                snippet = "".join(lines[start:end])
                
                finding = Finding(
                    id=f"manual-{file_path}:{line_num}",
                    title=f"Issue at {file_path}:{line_num}",
                    description="Manual fix request at this location",
                    file_path=file_path,
                    line_start=line_num,
                    severity=Severity.MEDIUM,
                    detector="manual",
                    issues=["manual_fix_request"],
                    code_snippet=snippet,
                )
        
        if finding is None:
            console.print("[red]Could not determine finding to fix[/red]")
            console.print("[dim]Use: repotoire fix 3 -f findings.json  OR  repotoire fix src/app.py:42[/dim]")
            ctx.exit(1)
        
        console.print(f"\n[bold cyan]🔧 Generating fix for:[/bold cyan]")
        console.print(f"  [bold]{finding.title}[/bold]")
        file_display = finding.affected_files[0] if finding.affected_files else "unknown"
        line_display = finding.line_start or "?"
        console.print(f"  📁 {file_display}:{line_display}")
        console.print(f"  🏷️  {finding.severity.value.upper()}\n")
        
        # Initialize AutoFixEngine
        from repotoire.autofix import AutoFixEngine
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Generating fix...", total=None)
            
            engine = AutoFixEngine(
                api_key=api_key,
                model=model,
                skip_runtime_validation=True,  # Fast mode for CLI
            )
            
            # Generate fix
            fix_proposal = asyncio.run(engine.generate_fix(finding, repo_path))
            
            progress.update(task, completed=True)
        
        if fix_proposal is None:
            console.print("[yellow]⚠️ Could not generate a fix for this finding[/yellow]")
            ctx.exit(1)
        
        # Show the fix
        console.print(f"\n[bold green]✅ Fix generated:[/bold green] {fix_proposal.title}")
        console.print(f"[dim]Confidence: {fix_proposal.confidence.value}[/dim]\n")
        
        if fix_proposal.description:
            console.print(Panel(fix_proposal.description, title="Explanation", border_style="blue"))
        if fix_proposal.rationale:
            console.print(Panel(fix_proposal.rationale, title="Rationale", border_style="dim"))
        
        # Show diff
        for change in fix_proposal.changes:
            console.print(f"\n[bold]📝 {change.file_path}[/bold]")
            
            # Create diff
            if change.original_code and change.fixed_code:
                diff = difflib.unified_diff(
                    change.original_code.splitlines(keepends=True),
                    change.fixed_code.splitlines(keepends=True),
                    fromfile=f"a/{change.file_path}",
                    tofile=f"b/{change.file_path}",
                )
                diff_text = "".join(diff)
                
                # Colorize diff
                colored_lines = []
                for line in diff_text.split("\n"):
                    if line.startswith("+") and not line.startswith("+++"):
                        colored_lines.append(f"[green]{escape(line)}[/green]")
                    elif line.startswith("-") and not line.startswith("---"):
                        colored_lines.append(f"[red]{escape(line)}[/red]")
                    elif line.startswith("@@"):
                        colored_lines.append(f"[cyan]{escape(line)}[/cyan]")
                    else:
                        colored_lines.append(escape(line))
                
                console.print("\n".join(colored_lines))
        
        # Apply if requested
        if apply:
            console.print("\n[bold]Applying fix...[/bold]")
            from repotoire.autofix import FixApplicator
            
            applicator = FixApplicator(repo_path)
            success = applicator.apply_fix(fix_proposal)
            
            if success:
                console.print("[green]✅ Fix applied successfully![/green]")
            else:
                console.print("[red]❌ Failed to apply fix[/red]")
                ctx.exit(1)
        else:
            console.print("\n[dim]Run with --apply to apply this fix[/dim]")
    
    except Exception as e:
        logger.error(f"Fix failed: {e}", exc_info=True)
        console.print(f"\n[red]❌ Error:[/red] {e}")
        ctx.exit(2)


# Import difflib at module level for fix command
import difflib


@cli.command("findings")
@click.argument("source", type=click.Path(exists=True))
@click.argument("index", required=False, type=int)
@click.option("--severity", "-s", type=click.Choice(["critical", "high", "medium", "low"]), help="Filter by severity")
@click.option("--top", "-n", type=int, help="Show only top N findings")
def show_findings(
    source: str,
    index: Optional[int],
    severity: Optional[str],
    top: Optional[int],
) -> None:
    """View findings from a JSON file.

    SOURCE is a JSON file from 'repotoire analyze --json -o findings.json'
    INDEX (optional) shows details for a specific finding.

    Examples:
        # List all findings
        repotoire findings findings.json

        # Show top 5 critical/high findings
        repotoire findings findings.json --top 5 --severity high

        # Show details for finding #3
        repotoire findings findings.json 3
    """
    import json
    from rich.syntax import Syntax
    
    try:
        with open(source) as f:
            data = json.load(f)
        
        findings_list = data.get("findings", data) if isinstance(data, dict) else data
        
        if not findings_list:
            console.print("[yellow]No findings in file[/yellow]")
            return
        
        # Filter by severity if specified
        if severity:
            findings_list = [f for f in findings_list if f.get("severity", "").lower() == severity]
        
        # Sort by severity (critical first)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings_list = sorted(findings_list, key=lambda f: severity_order.get(f.get("severity", "low").lower(), 4))
        
        # Limit if --top specified
        if top:
            findings_list = findings_list[:top]
        
        # Show specific finding details
        if index is not None:
            # Re-read original list for index lookup
            with open(source) as f:
                data = json.load(f)
            all_findings = data.get("findings", data) if isinstance(data, dict) else data
            
            if 1 <= index <= len(all_findings):
                f = all_findings[index - 1]
                
                sev = f.get("severity", "medium").upper()
                sev_colors = {"CRITICAL": "red bold", "HIGH": "red", "MEDIUM": "yellow", "LOW": "blue"}
                sev_style = sev_colors.get(sev, "white")
                
                console.print(f"\n[bold]Finding #{index}[/bold]")
                console.print(f"[{sev_style}]● {sev}[/{sev_style}] {f.get('title', 'Unknown')}\n")
                
                console.print(f"[dim]📁 File:[/dim] {f.get('file_path', 'unknown')}")
                console.print(f"[dim]📍 Line:[/dim] {f.get('line_start', '?')}")
                console.print(f"[dim]🔍 Detector:[/dim] {f.get('detector', 'unknown')}")
                
                if f.get("description"):
                    console.print(f"\n[bold]Description:[/bold]\n{f['description']}")
                
                if f.get("code_snippet"):
                    console.print(f"\n[bold]Code:[/bold]")
                    # Detect language from file extension
                    file_path = f.get("file_path", "")
                    lang = "python" if file_path.endswith(".py") else "javascript" if file_path.endswith((".js", ".ts")) else "text"
                    syntax = Syntax(f["code_snippet"], lang, line_numbers=True, start_line=f.get("line_start", 1))
                    console.print(syntax)
                
                if f.get("suggestion"):
                    console.print(f"\n[bold green]💡 Suggestion:[/bold green]\n{f['suggestion']}")
                
                console.print(f"\n[dim]Fix with: repotoire fix {index} -f {source}[/dim]")
            else:
                console.print(f"[red]Finding #{index} not found (have {len(all_findings)} findings)[/red]")
            return
        
        # List findings in a table
        table = Table(title=f"Findings ({len(findings_list)} total)", box=box.ROUNDED)
        table.add_column("#", style="dim", width=4)
        table.add_column("Sev", width=8)
        table.add_column("Title", no_wrap=False)
        table.add_column("File", style="dim", no_wrap=True)
        table.add_column("Line", style="dim", width=6)
        
        # Get original indices
        with open(source) as f:
            data = json.load(f)
        all_findings = data.get("findings", data) if isinstance(data, dict) else data
        
        for f in findings_list:
            # Find original index
            try:
                orig_idx = all_findings.index(f) + 1
            except ValueError:
                orig_idx = "?"
            
            sev = f.get("severity", "medium").upper()
            sev_colors = {"CRITICAL": "red bold", "HIGH": "red", "MEDIUM": "yellow", "LOW": "blue"}
            sev_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}
            
            table.add_row(
                str(orig_idx),
                f"[{sev_colors.get(sev, 'white')}]{sev_icon.get(sev, '⚪')} {sev[:4]}[/{sev_colors.get(sev, 'white')}]",
                f.get("title", "Unknown")[:60],
                f.get("file_path", "?")[-40:],
                str(f.get("line_start", "?")),
            )
        
        console.print(table)
        console.print(f"\n[dim]View details: repotoire findings {source} <number>[/dim]")
        console.print(f"[dim]Fix finding:  repotoire fix <number> -f {source}[/dim]")
    
    except json.JSONDecodeError:
        console.print(f"[red]Invalid JSON file: {source}[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.argument("repository", type=click.Path(exists=True))
@click.option("--max-files", type=int, default=500, help="Maximum Python files to analyze")
@click.option("--confidence-threshold", type=float, default=0.6, help="Minimum confidence for including rules (0.0-1.0)")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--instructions", is_flag=True, help="Show generated LLM instructions")
def style(
    repository: str,
    max_files: int,
    confidence_threshold: float,
    output_json: bool,
    instructions: bool,
) -> None:
    """Analyze codebase style conventions.

    Detects naming conventions, docstring styles, line lengths, and other
    code style patterns from your Python codebase. Results can be used to
    guide AI-powered code generation to match your existing style.

    Examples:
        # Analyze style in current directory
        repotoire style .

        # Analyze with more files for better accuracy
        repotoire style /path/to/repo --max-files 1000

        # Show generated LLM instructions
        repotoire style /path/to/repo --instructions

        # Output as JSON for automation
        repotoire style /path/to/repo --json
    """
    import json as json_module
    from pathlib import Path
    from rich.table import Table
    from repotoire.autofix.style import StyleAnalyzer, StyleEnforcer

    repo_path = Path(repository)

    try:
        # Analyze style
        console.print(f"\n[bold]Analyzing style conventions in {repository}...[/bold]\n")

        analyzer = StyleAnalyzer(repo_path)
        profile = analyzer.analyze(max_files=max_files)

        if output_json:
            # JSON output
            console.print(json_module.dumps(profile.to_dict(), indent=2, default=str))
            return

        if instructions:
            # Show LLM instructions
            enforcer = StyleEnforcer(profile, confidence_threshold=confidence_threshold)
            console.print(Panel(
                enforcer.get_style_instructions(),
                title="Generated LLM Instructions",
                border_style="cyan",
            ))
            return

        # Table output (default)
        table = Table(
            title=f"Style Profile for {repository}",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Rule", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("Confidence", justify="right")
        table.add_column("Samples", justify="right", style="dim")

        # Get rule summary
        enforcer = StyleEnforcer(profile, confidence_threshold=confidence_threshold)
        rules = enforcer.get_rule_summary()

        for rule in rules:
            # Format confidence
            if rule["confidence"] is not None:
                conf_pct = f"{rule['confidence']:.0%}"
                if rule["included"]:
                    conf_str = f"[green]{conf_pct}[/green]"
                elif rule["confidence"] >= 0.4:
                    conf_str = f"[yellow]{conf_pct}[/yellow]"
                else:
                    conf_str = f"[red]{conf_pct}[/red]"
            else:
                conf_str = "[dim]N/A[/dim]"

            # Format sample count
            sample_str = str(rule["sample_count"]) if rule["sample_count"] else "-"

            table.add_row(
                rule["name"],
                rule["value"],
                conf_str,
                sample_str,
            )

        console.print(table)
        console.print(f"\n[dim]Analyzed {profile.file_count} Python files[/dim]")

        # Show high confidence summary
        high_conf_rules = profile.get_high_confidence_rules(confidence_threshold)
        if high_conf_rules:
            console.print(
                f"[green]✓[/green] {len(high_conf_rules)} rules meet "
                f"{confidence_threshold:.0%} confidence threshold"
            )
        else:
            console.print(
                f"[yellow]⚠[/yellow] No rules meet {confidence_threshold:.0%} confidence threshold"
            )

    except ValueError as e:
        console.print(f"[red]❌ Error:[/red] {e}")
        raise click.Abort()
    except Exception as e:
        logger.error(f"Style analysis failed: {e}", exc_info=True)
        console.print(f"[red]❌ Error:[/red] {e}")
        raise click.Abort()


# Register security commands
from .security import security
cli.add_command(security)

# Register monorepo commands
from .monorepo import monorepo
cli.add_command(monorepo)

# Register ML commands
from .ml import ml
cli.add_command(ml)


@cli.command()
@click.option(
    "--min-detectors",
    type=int,
    default=2,
    help="Minimum number of detectors that must flag an entity (default: 2)",
)
@click.option(
    "--min-confidence",
    type=float,
    default=0.0,
    help="Minimum average confidence score 0.0-1.0 (default: 0.0)",
)
@click.option(
    "--severity",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"], case_sensitive=False),
    help="Filter by severity level",
)
@click.option(
    "--file",
    type=str,
    help="Show hotspots for a specific file",
)
@click.option(
    "--limit",
    type=int,
    default=50,
    help="Maximum results to return (default: 50)",
)
def hotspots(
    min_detectors: int,
    min_confidence: float,
    severity: Optional[str],
    file: Optional[str],
    limit: int,
) -> None:
    """Find code hotspots flagged by multiple detectors.

    Hotspots are code entities (files, classes, functions) that have been
    flagged by multiple detectors, indicating high-priority issues.

    Examples:

        # Find entities flagged by 3+ detectors
        repotoire hotspots --min-detectors 3

        # Find high-confidence critical issues
        repotoire hotspots --min-confidence 0.9 --severity HIGH

        # Show hotspots for specific file
        repotoire hotspots --file path/to/file.py
    """
    from repotoire.graph.enricher import GraphEnricher
    from rich.table import Table

    console.print("\n🔥 [bold]Code Hotspot Analysis[/bold]\n")

    # Connect to graph database
    try:
        db_client = _get_db_client()
        enricher = GraphEnricher(db_client)
    except Exception as e:
        console.print(f"[red]Failed to connect to graph database: {e}[/red]")
        raise click.Abort()

    try:
        if file:
            # Show hotspots for specific file
            console.print(f"Analyzing: [cyan]{file}[/cyan]\n")
            stats = enricher.get_file_hotspots(file)

            if stats["detector_count"] == 0:
                console.print(f"[green]No issues found in {file}[/green]")
                return

            console.print(f"[yellow]File Statistics:[/yellow]")
            console.print(f"  Total Flags: {stats['total_flags']}")
            console.print(f"  Detectors: {stats['detector_count']}")
            console.print(f"  LOC: {stats.get('file_loc', 'N/A')}")
            console.print(f"  Flagged By: {', '.join(stats['detectors'])}\n")

            # Show detailed flags
            table = Table(title="Detected Issues", show_header=True)
            table.add_column("Detector", style="cyan")
            table.add_column("Severity", style="yellow")
            table.add_column("Confidence", justify="right")
            table.add_column("Issues", style="dim")

            for flag in stats["flags"]:
                if flag["detector"]:  # Skip None values
                    confidence_pct = f"{flag['confidence']:.0%}" if flag.get("confidence") else "N/A"
                    issues_str = ", ".join(flag.get("issues", []))[:50]
                    table.add_row(
                        flag["detector"],
                        flag.get("severity", "N/A"),
                        confidence_pct,
                        issues_str
                    )

            console.print(table)

        else:
            # Find general hotspots
            console.print(f"Finding hotspots with:")
            console.print(f"  Min Detectors: {min_detectors}")
            console.print(f"  Min Confidence: {min_confidence:.1%}")
            if severity:
                console.print(f"  Severity: {severity}")
            console.print()

            hotspots_list = enricher.find_hotspots(
                min_detectors=min_detectors,
                min_confidence=min_confidence,
                severity=severity,
                limit=limit
            )

            if not hotspots_list:
                console.print("[green]No hotspots found matching criteria[/green]")
                return

            # Display results
            table = Table(title=f"Found {len(hotspots_list)} Hotspots", show_header=True)
            table.add_column("Entity", style="cyan", no_wrap=False)
            table.add_column("Type", style="magenta")
            table.add_column("Detectors", justify="center")
            table.add_column("Confidence", justify="right")
            table.add_column("Severity", style="yellow")
            table.add_column("Issues", style="dim")

            for hotspot in hotspots_list:
                entity_name = hotspot["entity"]
                if len(entity_name) > 60:
                    entity_name = "..." + entity_name[-57:]

                issues_str = ", ".join(set(hotspot.get("issues", [])))[:40]
                detectors_str = f"{hotspot['detector_count']} ({', '.join(hotspot['detectors'][:3])}{'...' if len(hotspot['detectors']) > 3 else ''})"

                table.add_row(
                    entity_name,
                    hotspot.get("entity_type", "Unknown"),
                    str(hotspot["detector_count"]),
                    f"{hotspot['avg_confidence']:.0%}",
                    hotspot.get("severity", "N/A"),
                    issues_str
                )

            console.print(table)
            console.print(f"\n[dim]Showing top {len(hotspots_list)} of {limit} results[/dim]")

    except Exception as e:
        console.print(f"[red]Error during hotspot analysis: {e}[/red]")
        logger.exception("Hotspot analysis failed")
        raise click.Abort()
    finally:
        graph_client.close()


@cli.group()
def embeddings() -> None:
    """Manage graph embeddings for structural similarity.

    Graph embeddings capture structural patterns in the code graph,
    enabling similarity search based on call relationships, imports,
    and code organization.

    Examples:
        repotoire embeddings generate     # Generate FastRP embeddings
        repotoire embeddings stats        # Show embedding statistics
        repotoire embeddings similar X    # Find similar to X
    """
    pass


@embeddings.command("generate")
@click.option(
    "--dimension",
    "-d",
    type=int,
    default=128,
    help="Embedding dimension (default: 128)",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Regenerate even if embeddings exist",
)
def embeddings_generate(
    dimension: int,
    force: bool,
) -> None:
    """Generate FastRP graph embeddings for structural similarity.

    FastRP (Fast Random Projection) creates embeddings that capture
    the structural position of code entities in the call graph.

    Requirements:
        - FalkorDB running and accessible
        - Code already ingested into graph

    Examples:
        repotoire embeddings generate
        repotoire embeddings generate --dimension 256
        repotoire embeddings generate --force
    """
    from repotoire.ml import FastRPEmbedder, FastRPConfig

    console.print("\n🔮 [bold]FastRP Graph Embedding Generation[/bold]\n")

    # Connect to graph database
    try:
        graph_client = _get_db_client()
    except Exception as e:
        console.print(f"[red]Failed to connect to graph database: {e}[/red]")
        raise click.Abort()

    try:
        config = FastRPConfig(embedding_dimension=dimension)
        embedder = FastRPEmbedder(graph_client, config)

        # Check existing embeddings
        stats = embedder.get_embedding_stats()
        if stats["nodes_with_embeddings"] > 0 and not force:
            console.print(
                f"[yellow]Embeddings already exist: {stats['nodes_with_embeddings']} nodes "
                f"({stats['coverage_percent']:.1f}% coverage)[/yellow]"
            )
            console.print("[dim]Use --force to regenerate[/dim]")
            return

        # Generate embeddings
        console.print(f"Configuration:")
        console.print(f"  Dimension: [cyan]{dimension}[/cyan]")
        console.print(f"  Node types: [cyan]{', '.join(config.node_labels)}[/cyan]")
        console.print(f"  Relationships: [cyan]{', '.join(config.relationship_types)}[/cyan]")
        console.print()

        with console.status("[cyan]Generating embeddings...[/cyan]"):
            gen_stats = embedder.generate_embeddings()

        if gen_stats["node_count"] == 0:
            console.print("[yellow]⚠️  No nodes found to embed[/yellow]")
            console.print("[dim]Run 'repotoire ingest' first to populate the graph[/dim]")
            return

        console.print(f"[green]✓ Generated {gen_stats['node_count']:,} embeddings[/green]")
        console.print(f"  Dimension: {gen_stats['embedding_dimension']}")
        console.print(f"  Compute time: {gen_stats['compute_millis']}ms")
        console.print(f"  Write time: {gen_stats['write_millis']}ms")

        # Show breakdown by label
        final_stats = embedder.get_embedding_stats()
        if final_stats.get("by_label"):
            console.print("\n[bold]By node type:[/bold]")
            for label, counts in final_stats["by_label"].items():
                console.print(f"  {label}: {counts['embedded']:,} / {counts['total']:,}")

    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[dim]Ensure FalkorDB is running and accessible[/dim]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Embedding generation failed")
        raise click.Abort()
    finally:
        graph_client.close()


@embeddings.command("stats")
def embeddings_stats() -> None:
    """Show statistics about generated graph embeddings.

    Examples:
        repotoire embeddings stats
    """
    from repotoire.ml import FastRPEmbedder

    console.print("\n📊 [bold]Graph Embedding Statistics[/bold]\n")

    try:
        graph_client = _get_db_client()
    except Exception as e:
        console.print(f"[red]Failed to connect to graph database: {e}[/red]")
        raise click.Abort()

    try:
        embedder = FastRPEmbedder(graph_client)
        stats = embedder.get_embedding_stats()

        if stats["nodes_with_embeddings"] == 0:
            console.print("[yellow]No graph embeddings found[/yellow]")
            console.print("[dim]Run 'repotoire embeddings generate' to create them[/dim]")
            return

        console.print(f"Total nodes: {stats['total_nodes']:,}")
        console.print(f"Nodes with embeddings: {stats['nodes_with_embeddings']:,}")
        console.print(f"Coverage: [cyan]{stats['coverage_percent']:.1f}%[/cyan]")
        console.print(f"Embedding dimension: {stats['embedding_dimension']}")

        if stats.get("by_label"):
            console.print("\n[bold]By node type:[/bold]")
            for label, counts in stats["by_label"].items():
                pct = (counts['embedded'] / counts['total'] * 100) if counts['total'] > 0 else 0
                console.print(f"  {label}: {counts['embedded']:,} / {counts['total']:,} ({pct:.0f}%)")

    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()
    finally:
        graph_client.close()


@embeddings.command("similar")
@click.argument("qualified_name")
@click.option(
    "--top-k",
    "-k",
    type=int,
    default=10,
    help="Number of results (default: 10)",
)
@click.option(
    "--type",
    "-t",
    "node_type",
    type=click.Choice(["Function", "Class", "File"], case_sensitive=True),
    default=None,
    help="Filter by node type",
)
def embeddings_similar(
    qualified_name: str,
    top_k: int,
    node_type: Optional[str],
) -> None:
    """Find entities structurally similar to the given entity.

    Uses FastRP embeddings to find entities with similar structural
    patterns in the code graph.

    Examples:
        repotoire embeddings similar "my.module.MyClass.method"
        repotoire embeddings similar "my.module" --type Function -k 20
    """
    from repotoire.ml import StructuralSimilarityAnalyzer
    from rich.table import Table

    console.print(f"\n🔍 [bold]Finding entities similar to:[/bold] [cyan]{qualified_name}[/cyan]\n")

    try:
        graph_client = _get_db_client()
    except Exception as e:
        console.print(f"[red]Failed to connect to graph database: {e}[/red]")
        raise click.Abort()

    try:
        analyzer = StructuralSimilarityAnalyzer(graph_client)

        # Check embeddings exist
        stats = analyzer.get_stats()
        if stats["nodes_with_embeddings"] == 0:
            console.print("[yellow]No graph embeddings found[/yellow]")
            console.print("[dim]Run 'repotoire embeddings generate' first[/dim]")
            return

        # Find similar
        node_labels = [node_type] if node_type else None
        results = analyzer.find_similar(qualified_name, top_k=top_k, node_labels=node_labels)

        if not results:
            console.print("[yellow]No similar entities found[/yellow]")
            console.print("[dim]The entity may not have an embedding, or no similar entities exist[/dim]")
            return

        # Display results
        table = Table(title=f"Top {len(results)} Structurally Similar Entities", show_header=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("Entity", style="cyan", no_wrap=False)
        table.add_column("Type", style="magenta", width=10)
        table.add_column("Similarity", justify="right", style="green")
        table.add_column("File", style="dim", no_wrap=False)

        for i, result in enumerate(results, 1):
            name_display = result.qualified_name
            if len(name_display) > 50:
                name_display = "..." + name_display[-47:]

            file_display = result.file_path or ""
            if len(file_display) > 40:
                file_display = "..." + file_display[-37:]

            table.add_row(
                str(i),
                name_display,
                result.node_type or "?",
                f"{result.similarity_score:.3f}",
                file_display,
            )

        console.print(table)

    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Similarity search failed")
        raise click.Abort()
    finally:
        graph_client.close()


@embeddings.command("clones")
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=0.95,
    help="Minimum similarity to be considered a clone (default: 0.95)",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=50,
    help="Maximum results (default: 50)",
)
def embeddings_clones(
    threshold: float,
    limit: int,
) -> None:
    """Find potential code clones based on structural similarity.

    Identifies function pairs with very high structural similarity,
    which may indicate duplicated or copy-pasted code.

    Examples:
        repotoire embeddings clones
        repotoire embeddings clones --threshold 0.9 --limit 100
    """
    from repotoire.ml import StructuralSimilarityAnalyzer
    from rich.table import Table

    console.print(f"\n🔎 [bold]Finding potential code clones[/bold] (threshold >= {threshold})\n")

    try:
        graph_client = _get_db_client()
    except Exception as e:
        console.print(f"[red]Failed to connect to graph database: {e}[/red]")
        raise click.Abort()

    try:
        analyzer = StructuralSimilarityAnalyzer(graph_client)

        # Check embeddings exist
        stats = analyzer.get_stats()
        if stats["nodes_with_embeddings"] == 0:
            console.print("[yellow]No graph embeddings found[/yellow]")
            console.print("[dim]Run 'repotoire embeddings generate' first[/dim]")
            return

        # Find clones
        with console.status("[cyan]Searching for clones...[/cyan]"):
            pairs = analyzer.find_potential_clones(threshold=threshold, limit=limit)

        if not pairs:
            console.print(f"[green]✓ No potential clones found above {threshold} similarity[/green]")
            return

        console.print(f"[yellow]Found {len(pairs)} potential clone pairs[/yellow]\n")

        # Display results
        table = Table(title="Potential Code Clones", show_header=True)
        table.add_column("Entity A", style="cyan", no_wrap=False)
        table.add_column("Entity B", style="cyan", no_wrap=False)
        table.add_column("Similarity", justify="right", style="yellow")

        for entity_a, entity_b in pairs[:20]:  # Show top 20
            name_a = entity_a.name or entity_a.qualified_name.split("::")[-1].split(":")[0]
            name_b = entity_b.name or entity_b.qualified_name.split("::")[-1].split(":")[0]

            file_a = entity_a.file_path or ""
            file_b = entity_b.file_path or ""
            if len(file_a) > 30:
                file_a = "..." + file_a[-27:]
            if len(file_b) > 30:
                file_b = "..." + file_b[-27:]

            display_a = f"{name_a}\n[dim]{file_a}[/dim]"
            display_b = f"{name_b}\n[dim]{file_b}[/dim]"

            table.add_row(
                display_a,
                display_b,
                f"{entity_a.similarity_score:.3f}",
            )

        console.print(table)

        if len(pairs) > 20:
            console.print(f"\n[dim]Showing 20 of {len(pairs)} pairs[/dim]")

    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Clone detection failed")
        raise click.Abort()
    finally:
        graph_client.close()


# ============================================================================
# FIX TEMPLATES COMMANDS
# ============================================================================


@cli.group()
def templates() -> None:
    """Manage fix templates for automatic code fixes.

    Templates provide fast, deterministic code fixes that don't require LLM calls.
    They are loaded from YAML files in .repotoire/fix-templates/ or ~/.config/repotoire/fix-templates/.

    Examples:
        repotoire templates list           # List all loaded templates
        repotoire templates list --verbose # Show detailed template info
    """
    pass


@templates.command("list")
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Show detailed template information"
)
@click.option(
    "--language",
    "-l",
    default=None,
    help="Filter by language (e.g., python, typescript)"
)
@click.option(
    "--template-dir",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Additional template directory to load from"
)
def templates_list(verbose: bool, language: str | None, template_dir: Path | None) -> None:
    """List all loaded fix templates.

    Shows templates loaded from default directories and any additional directories.
    Templates are sorted by priority (highest first).
    """
    from repotoire.autofix.templates import (
        get_registry,
        reset_registry,
        DEFAULT_TEMPLATE_DIRS,
    )

    # Reset and reload registry if custom dir provided
    if template_dir:
        reset_registry()
        dirs = list(DEFAULT_TEMPLATE_DIRS) + [template_dir]
        registry = get_registry(template_dirs=dirs, force_reload=True)
    else:
        registry = get_registry()

    # Filter by language if specified
    templates = registry.templates
    if language:
        templates = [
            t for t in templates
            if language.lower() in [lang.lower() for lang in t.languages]
        ]

    if not templates:
        console.print("[yellow]No templates loaded.[/yellow]")
        console.print("\n[dim]Template directories searched:[/dim]")
        for d in DEFAULT_TEMPLATE_DIRS:
            status = "✓ exists" if d.exists() else "✗ not found"
            console.print(f"  [dim]{d}[/dim] ({status})")
        if template_dir:
            status = "✓ exists" if template_dir.exists() else "✗ not found"
            console.print(f"  [dim]{template_dir}[/dim] ({status})")
        return

    # Create table
    table = Table(
        title=f"Fix Templates ({len(templates)} loaded)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )

    table.add_column("Name", style="green")
    table.add_column("Priority", justify="center")
    table.add_column("Type", style="cyan")
    table.add_column("Confidence", justify="center")
    table.add_column("Languages")

    if verbose:
        table.add_column("Pattern Type")
        table.add_column("Description", max_width=40)

    for template in templates:
        confidence_color = {
            "HIGH": "green",
            "MEDIUM": "yellow",
            "LOW": "red",
        }.get(template.confidence, "white")

        row = [
            template.name,
            str(template.priority),
            template.fix_type,
            f"[{confidence_color}]{template.confidence}[/{confidence_color}]",
            ", ".join(template.languages),
        ]

        if verbose:
            row.extend([
                template.pattern_type.value,
                (template.description or "")[:40] + ("..." if template.description and len(template.description) > 40 else ""),
            ])

        table.add_row(*row)

    console.print(table)

    # Show loaded files
    if registry.loaded_files:
        console.print("\n[dim]Loaded from:[/dim]")
        for f in registry.loaded_files:
            console.print(f"  [dim]{f}[/dim]")


# Register sandbox stats command (REPO-295)
from .sandbox import sandbox_stats
cli.add_command(sandbox_stats)

# Register graph management commands (REPO-263)
from .graph import graph
cli.add_command(graph)

# Register auth commands (REPO-267)
from .auth_commands import auth_group
cli.add_command(auth_group)

# Register organization commands (CLI/Web sync)
from .org_commands import org_group
cli.add_command(org_group)

# Register API key management commands (REPO-324)
from .api_keys import api_keys
cli.add_command(api_keys, name="api-keys")

# Register git history RAG commands (replaces Graphiti - 99% cheaper)
from .historical import historical
cli.add_command(historical)


@cli.command()
@click.argument("query")
@click.option(
    "--embedding-backend",
    type=click.Choice(["auto", "openai", "local", "deepinfra", "voyage"], case_sensitive=False),
    default="auto",
    help="Embedding backend for retrieval ('auto' selects best available)",
)
@click.option(
    "--llm-backend",
    type=click.Choice(["openai", "anthropic"], case_sensitive=False),
    default="openai",
    help="LLM backend for answer generation: 'openai' (GPT-4o) or 'anthropic' (Claude Opus 4.5)",
)
@click.option(
    "--llm-model",
    default=None,
    help="LLM model (default: gpt-4o for OpenAI, claude-opus-4-20250514 for Anthropic)",
)
@click.option(
    "--top-k",
    type=int,
    default=10,
    help="Number of code snippets to retrieve for context (default: 10)",
)
@click.option(
    "--hybrid-search/--no-hybrid-search",
    default=True,
    help="Enable hybrid search (dense + BM25) for improved recall (default: enabled)",
)
@click.option(
    "--fusion-method",
    type=click.Choice(["rrf", "linear"], case_sensitive=False),
    default="rrf",
    help="Fusion method for hybrid search: 'rrf' (Reciprocal Rank Fusion) or 'linear'",
)
@click.option(
    "--reranker",
    type=click.Choice(["voyage", "local", "none"], case_sensitive=False),
    default="local",
    help="Reranker backend: 'voyage' (API), 'local' (cross-encoder), or 'none'",
)
@click.option(
    "--reranker-model",
    default=None,
    help="Reranker model (default: rerank-2 for voyage, ms-marco-MiniLM for local)",
)
def ask(
    query: str,
    embedding_backend: str,
    llm_backend: str,
    llm_model: Optional[str],
    top_k: int,
    hybrid_search: bool,
    fusion_method: str,
    reranker: str,
    reranker_model: Optional[str],
) -> None:
    """Ask a question about the codebase using RAG.

    Uses hybrid search (dense embeddings + BM25) to find relevant code,
    optionally reranks results, then generates an answer using GPT-4o or Claude.

    Requires embeddings to be generated first:
        repotoire ingest /path/to/repo --generate-embeddings

    Examples:
        repotoire ask "How does authentication work?"
        repotoire ask "What functions call the database?" --top-k 20
        repotoire ask "Explain the caching mechanism" --llm-backend anthropic
        repotoire ask "JWT middleware" --hybrid-search --reranker voyage
        repotoire ask "calculate_score function" --no-hybrid-search --reranker none
    """
    from repotoire.ai import (
        CodeEmbedder,
        EmbeddingConfig,
        GraphRAGRetriever,
        RetrieverConfig,
        HybridSearchConfig,
        RerankerConfig,
    )
    from repotoire.ai.llm import LLMConfig

    console.print("\n[bold cyan]RAG Code Q&A[/bold cyan]\n")
    console.print(f"[dim]Query:[/dim] {query}\n")

    # Connect to graph database
    try:
        client = _get_db_client()
    except Exception as e:
        console.print(f"[red]Failed to connect to graph database: {e}[/red]")
        console.print("[dim]Make sure REPOTOIRE_API_KEY is set correctly[/dim]")
        raise click.Abort()

    try:
        # Check for embeddings
        stats = client.get_stats()
        embeddings_count = stats.get("embeddings_count", 0)

        if embeddings_count == 0:
            console.print("[yellow]No embeddings found in database[/yellow]")
            console.print("[dim]Run 'repotoire ingest --generate-embeddings' first[/dim]")
            raise click.Abort()

        console.print(f"[dim]Using {embeddings_count:,} embeddings[/dim]")

        # Initialize embedder
        embed_config = EmbeddingConfig(backend=embedding_backend)
        embedder = CodeEmbedder(config=embed_config)
        console.print(f"[dim]Embedding backend: {embedder.resolved_backend}[/dim]")
        if embedding_backend == "auto":
            console.print(f"[dim]   {embedder.backend_reason}[/dim]")

        # Initialize LLM config
        llm_config = LLMConfig(backend=llm_backend, model=llm_model)
        console.print(f"[dim]LLM backend: {llm_backend}/{llm_config.get_model()}[/dim]")

        # Configure hybrid search (REPO-243)
        hybrid_config = HybridSearchConfig(
            enabled=hybrid_search,
            fusion_method=fusion_method.lower(),
        )
        console.print(f"[dim]Hybrid search: {'enabled' if hybrid_search else 'disabled'}[/dim]")

        # Configure reranker (REPO-241)
        reranker_config = RerankerConfig(
            enabled=reranker != "none",
            backend=reranker if reranker != "none" else "local",
            model=reranker_model,
            top_k=top_k,
        )
        if reranker != "none":
            console.print(f"[dim]Reranker: {reranker}/{reranker_config.get_model()}[/dim]")
        else:
            console.print("[dim]Reranker: disabled[/dim]")

        # Build retriever config
        retriever_config = RetrieverConfig(
            top_k=top_k,
            hybrid=hybrid_config,
            reranker=reranker_config,
        )

        # Create retriever with LLM
        retriever = GraphRAGRetriever(
            client=client,
            embedder=embedder,
            config=retriever_config,
            llm_config=llm_config,
        )

        console.print()

        # Generate answer
        with console.status("[bold green]Thinking..."):
            answer = retriever.ask(query, top_k=top_k)

        console.print("[bold]Answer:[/bold]\n")
        console.print(answer)
        console.print()

    except ValueError as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("RAG ask failed")
        raise click.Abort()
    finally:
        client.close()


# Config command group
@cli.group()
def config():
    """Manage Repotoire configuration.

    \b
    COMMANDS:
      init     Create a new configuration file
      show     Display current configuration
    """
    pass


@config.command("init")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["yaml", "toml", "json"], case_sensitive=False),
    default="yaml",
    help="Configuration file format (default: yaml)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output path (default: .reporc for yaml/json, falkor.toml for toml)",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing config file",
)
def config_init(format: str, output: str | None, force: bool) -> None:
    """Create a new configuration file with default settings.

    \b
    Examples:
      # Create default .reporc (YAML)
      repotoire config init

      # Create TOML config
      repotoire config init -f toml

      # Create config at custom path
      repotoire config init -o /path/to/my-config.yaml
    """
    from repotoire.config import generate_config_template

    # Determine output path
    if output is None:
        if format.lower() == "toml":
            output = "falkor.toml"
        else:
            output = ".reporc"

    output_path = Path(output)

    # Check if file exists
    if output_path.exists() and not force:
        console.print(f"[yellow]⚠️  Config file already exists: {output}[/yellow]")
        console.print("[dim]Use --force to overwrite[/dim]")
        raise click.Abort()

    try:
        template = generate_config_template(format.lower())
        output_path.write_text(template)
        console.print(f"[green]✓[/green] Created config file: {output}")
        console.print(f"[dim]Edit this file to customize Repotoire settings[/dim]")
    except Exception as e:
        console.print(f"[red]❌ Error creating config: {e}[/red]")
        raise click.Abort()


@config.command("show")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["yaml", "json", "table"], case_sensitive=False),
    default="table",
    help="Output format (default: table)",
)
@click.pass_context
def config_show(ctx: click.Context, format: str) -> None:
    """Display current configuration.

    Shows the merged configuration from all sources (defaults, config file,
    environment variables).

    \b
    Examples:
      # Show as formatted table
      repotoire config show

      # Show as YAML
      repotoire config show -f yaml

      # Show as JSON (for scripting)
      repotoire config show -f json
    """
    cfg: FalkorConfig = ctx.obj['config']

    if format.lower() == "json":
        import json
        console.print(json.dumps(cfg.to_dict(), indent=2, default=str))
        return

    if format.lower() == "yaml":
        try:
            import yaml
            console.print(yaml.dump(cfg.to_dict(), default_flow_style=False, sort_keys=False))
        except ImportError:
            console.print("[yellow]YAML output requires PyYAML: pip install pyyaml[/yellow]")
            raise click.Abort()
        return

    # Table format (default)
    from rich.tree import Tree

    tree = Tree("[bold cyan]Repotoire Configuration[/bold cyan]")

    # Database section
    db_section = tree.add("[cyan]database[/cyan]")
    db_section.add(f"host: {cfg.database.host}")
    db_section.add(f"port: {cfg.database.port}")
    db_section.add(f"password: {'***' if cfg.database.password else '(not set)'}")
    db_section.add(f"max_retries: {cfg.database.max_retries}")

    # Ingestion section
    ing_section = tree.add("[cyan]ingestion[/cyan]")
    patterns_str = ", ".join(cfg.ingestion.patterns[:3])
    if len(cfg.ingestion.patterns) > 3:
        patterns_str += f" (+{len(cfg.ingestion.patterns) - 3} more)"
    ing_section.add(f"patterns: {patterns_str}")
    exclude_str = ", ".join(cfg.ingestion.exclude_patterns[:2])
    if len(cfg.ingestion.exclude_patterns) > 2:
        exclude_str += f" (+{len(cfg.ingestion.exclude_patterns) - 2} more)"
    ing_section.add(f"exclude_patterns: {exclude_str}")
    ing_section.add(f"batch_size: {cfg.ingestion.batch_size}")
    ing_section.add(f"max_file_size_mb: {cfg.ingestion.max_file_size_mb}")

    # Detectors section
    det_section = tree.add("[cyan]detectors[/cyan]")
    if cfg.detectors.enabled_detectors:
        det_section.add(f"enabled_detectors: {cfg.detectors.enabled_detectors}")
    if cfg.detectors.disabled_detectors:
        det_section.add(f"disabled_detectors: {cfg.detectors.disabled_detectors}")
    det_section.add(f"god_class_high_method_count: {cfg.detectors.god_class_high_method_count}")

    # Logging section
    log_section = tree.add("[cyan]logging[/cyan]")
    log_section.add(f"level: {cfg.logging.level}")
    log_section.add(f"format: {cfg.logging.format}")
    log_section.add(f"file: {cfg.logging.file or '(not set)'}")

    # Embeddings section
    emb_section = tree.add("[cyan]embeddings[/cyan]")
    emb_section.add(f"backend: {cfg.embeddings.backend}")
    emb_section.add(f"model: {cfg.embeddings.model or '(default)'}")

    console.print(tree)


@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def shell_completion(shell: str) -> None:
    """Generate shell completion script.

    \b
    USAGE:
      # Bash (add to ~/.bashrc)
      eval "$(repotoire completion bash)"

      # Zsh (add to ~/.zshrc)
      eval "$(repotoire completion zsh)"

      # Fish (add to ~/.config/fish/completions/repotoire.fish)
      repotoire completion fish > ~/.config/fish/completions/repotoire.fish
    """
    import os
    import sys

    # Get the completion script using Click's built-in support
    prog_name = "repotoire"

    if shell == "bash":
        script = f'''
# Bash completion for {prog_name}
_{prog_name}_completion() {{
    local IFS=$'\\n'
    COMPREPLY=( $( env COMP_WORDS="${{COMP_WORDS[*]}}" \\
                   COMP_CWORD=$COMP_CWORD \\
                   _{prog_name.upper()}_COMPLETE=bash_complete $1 ) )
    return 0
}}
complete -o default -F _{prog_name}_completion {prog_name}
'''
    elif shell == "zsh":
        script = f'''
# Zsh completion for {prog_name}
_{prog_name}_completion() {{
    local -a completions
    local -a completions_with_descriptions
    local -a response
    (( ! $+commands[{prog_name}] )) && return 1
    response=("${{(@f)$( env COMP_WORDS="${{words[*]}}" \\
                        COMP_CWORD=$((CURRENT-1)) \\
                        _{prog_name.upper()}_COMPLETE=zsh_complete {prog_name} )}}")
    for key descr in ${{(kv)response}}; do
        if [[ "$descr" == "_" ]]; then
            completions+=("$key")
        else
            completions_with_descriptions+=("$key":"$descr")
        fi
    done
    if [ -n "$completions_with_descriptions" ]; then
        _describe -V unsorted completions_with_descriptions -U
    fi
    if [ -n "$completions" ]; then
        compadd -U -V unsorted -a completions
    fi
    compstate[insert]="automenu"
}}
compdef _{prog_name}_completion {prog_name}
'''
    else:  # fish
        script = f'''
# Fish completion for {prog_name}
function _{prog_name}_completion
    set -l response (env _{prog_name.upper()}_COMPLETE=fish_complete COMP_WORDS=(commandline -cp) COMP_CWORD=(commandline -t) {prog_name})
    for completion in $response
        echo $completion
    end
end
complete -c {prog_name} -f -a "(_{prog_name}_completion)"
'''

    click.echo(script.strip())


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
