"""Marketplace CLI commands.

This module provides the `repotoire marketplace` command group with
commands for browsing, installing, syncing, and publishing marketplace assets.

Usage:
    repotoire marketplace search "code review"
    repotoire marketplace browse --sort=popular
    repotoire marketplace info @repotoire/review-pr
    repotoire marketplace install @repotoire/review-pr
    repotoire marketplace sync
    repotoire marketplace publish @me/my-command ./command.md

Claude Integration:
    repotoire marketplace sync-claude     # Sync all assets to Claude config
    repotoire marketplace claude-status   # Show Claude config status
    repotoire marketplace export          # Export assets for Claude.ai
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.text import Text

from repotoire.cli.marketplace_client import (
    AssetInfo,
    AssetNotFoundError,
    AuthenticationError,
    MarketplaceAPIClient,
    MarketplaceAPIError,
    TierLimitError,
    format_install_count,
    parse_asset_reference,
)
from repotoire.cli.marketplace_sync import (
    extract_asset,
    get_asset_path,
    get_local_manifest,
    remove_asset_files,
    remove_from_manifest,
    update_manifest,
    ensure_directories_exist,
    SyncResult,
)
from repotoire.logging_config import get_logger
from repotoire.marketplace import (
    ClaudeConfigManager,
    ClaudeConfigError,
    ExportedAsset,
    export_as_project_instructions,
    generate_clipboard_text,
)

logger = get_logger(__name__)
console = Console()


def _get_claude_manager() -> ClaudeConfigManager:
    """Get the Claude config manager instance."""
    return ClaudeConfigManager()


def _get_client() -> MarketplaceAPIClient:
    """Get the marketplace API client.

    Raises:
        click.ClickException: If API key is not configured.
    """
    try:
        return MarketplaceAPIClient()
    except AuthenticationError as e:
        raise click.ClickException(str(e))


def _format_rating(rating: float | None) -> str:
    """Format rating for display."""
    if rating is None:
        return "-"
    return f"★ {rating:.1f}"


def _format_pricing(pricing: str) -> Text:
    """Format pricing with color."""
    if pricing == "free":
        return Text("free", style="green")
    elif pricing == "pro":
        return Text("pro", style="cyan")
    elif pricing == "paid":
        return Text("paid", style="yellow")
    else:
        return Text(pricing)


def _create_results_table(assets: list[AssetInfo], show_installed: bool = True) -> Table:
    """Create a Rich table for displaying asset search/browse results."""
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )

    table.add_column("Asset", style="white")
    table.add_column("Type", style="dim")
    table.add_column("Rating", justify="center")
    table.add_column("Installs", justify="right")
    table.add_column("Pricing", justify="center")

    for asset in assets:
        # Add checkmark if installed
        name = asset.full_name
        if show_installed and asset.is_installed:
            name = f"✓ {name}"
            name_style = "green"
        else:
            name_style = "white"

        table.add_row(
            Text(name, style=name_style),
            asset.asset_type,
            _format_rating(asset.rating),
            format_install_count(asset.install_count),
            _format_pricing(asset.pricing),
        )

    return table


@click.group()
def marketplace():
    """Marketplace commands for browsing, installing, and publishing assets.

    \b
    BROWSE & SEARCH:
      repotoire marketplace search "code review"
      repotoire marketplace browse --sort=popular
      repotoire marketplace info @repotoire/review-pr

    \b
    INSTALL & MANAGE:
      repotoire marketplace install @repotoire/review-pr
      repotoire marketplace uninstall @repotoire/review-pr
      repotoire marketplace list
      repotoire marketplace sync

    \b
    PUBLISH:
      repotoire marketplace publish @me/my-command ./command.md 1.0.0
    """
    pass


# =============================================================================
# Browse & Search Commands
# =============================================================================


@marketplace.command()
@click.argument("query")
@click.option(
    "--type", "-t",
    type=click.Choice(["command", "skill", "style", "hook", "prompt"]),
    help="Filter by asset type",
)
@click.option(
    "--category", "-c",
    type=str,
    help="Filter by category",
)
@click.option(
    "--limit", "-l",
    type=int,
    default=20,
    help="Maximum number of results (default: 20)",
)
def search(query: str, type: str | None, category: str | None, limit: int):
    """Search for marketplace assets.

    \b
    Examples:
      repotoire marketplace search "code review"
      repotoire marketplace search "security" --type=command
      repotoire marketplace search "sql" --limit=50
    """
    client = _get_client()

    with console.status(f"Searching for '{query}'..."):
        try:
            assets = client.search(
                query=query,
                asset_type=type,
                category=category,
                limit=limit,
            )
        except MarketplaceAPIError as e:
            raise click.ClickException(
                f"Search failed: {e.message}\n"
                "Check your internet connection and try again."
            )

    if not assets:
        console.print(f"[yellow]No assets found matching '{query}'[/yellow]")
        return

    console.print(f"\n[bold]Found {len(assets)} asset(s):[/bold]\n")
    table = _create_results_table(assets)
    console.print(table)


@marketplace.command()
@click.option(
    "--sort", "-s",
    type=click.Choice(["popular", "recent", "rating", "trending"]),
    default="popular",
    help="Sort order (default: popular)",
)
@click.option(
    "--type", "-t",
    type=click.Choice(["command", "skill", "style", "hook", "prompt"]),
    help="Filter by asset type",
)
@click.option(
    "--category", "-c",
    type=str,
    help="Filter by category",
)
@click.option(
    "--limit", "-l",
    type=int,
    default=20,
    help="Maximum number of results (default: 20)",
)
def browse(sort: str, type: str | None, category: str | None, limit: int):
    """Browse marketplace assets.

    \b
    Examples:
      repotoire marketplace browse
      repotoire marketplace browse --sort=recent
      repotoire marketplace browse --type=skill --sort=rating
    """
    client = _get_client()

    with console.status("Loading marketplace..."):
        try:
            assets = client.browse(
                sort=sort,
                asset_type=type,
                category=category,
                limit=limit,
            )
        except MarketplaceAPIError as e:
            raise click.ClickException(
                f"Failed to browse marketplace: {e.message}\n"
                "Check your internet connection and try again."
            )

    if not assets:
        console.print("[yellow]No assets found[/yellow]")
        return

    sort_label = {
        "popular": "Most Popular",
        "recent": "Recently Updated",
        "rating": "Top Rated",
        "trending": "Trending",
    }.get(sort, sort.title())

    console.print(f"\n[bold]{sort_label} Assets:[/bold]\n")
    table = _create_results_table(assets)
    console.print(table)


@marketplace.command()
@click.argument("asset_ref")
@click.option(
    "--versions", "-v",
    is_flag=True,
    help="Show all versions",
)
def info(asset_ref: str, versions: bool):
    """Show detailed information about an asset.

    \b
    ASSET_REF: Asset reference (e.g., @publisher/slug)

    \b
    Examples:
      repotoire marketplace info @repotoire/review-pr
      repotoire marketplace info @user/my-skill --versions
    """
    try:
        publisher, slug, _ = parse_asset_reference(asset_ref)
    except ValueError as e:
        raise click.ClickException(str(e))

    client = _get_client()

    try:
        with console.status(f"Loading {asset_ref}..."):
            asset = client.get_asset(publisher, slug)

            version_list = []
            if versions:
                version_list = client.get_asset_versions(publisher, slug)

    except AssetNotFoundError:
        raise click.ClickException(
            f"Asset {asset_ref} not found.\n"
            f"Search for similar: repotoire marketplace search '{slug}'"
        )
    except MarketplaceAPIError as e:
        raise click.ClickException(f"Failed to get asset info: {e.message}")

    # Build info panel
    info_lines = [
        f"[bold]Name:[/bold] {asset.name}",
        f"[bold]Type:[/bold] {asset.asset_type}",
        f"[bold]Publisher:[/bold] @{asset.publisher_slug}",
        f"[bold]Latest Version:[/bold] {asset.latest_version or 'N/A'}",
        f"[bold]Rating:[/bold] {_format_rating(asset.rating)}",
        f"[bold]Installs:[/bold] {format_install_count(asset.install_count)}",
        f"[bold]Pricing:[/bold] {asset.pricing}",
        "",
        f"[bold]Description:[/bold]",
        asset.description or "No description provided.",
    ]

    if asset.is_installed:
        info_lines.insert(0, "[green]✓ Installed[/green]\n")

    panel = Panel(
        "\n".join(info_lines),
        title=asset.full_name,
        border_style="cyan",
        box=box.ROUNDED,
    )
    console.print(panel)

    # Show versions if requested
    if versions and version_list:
        console.print("\n[bold]Versions:[/bold]")
        version_table = Table(box=box.SIMPLE, show_header=True)
        version_table.add_column("Version", style="cyan")
        version_table.add_column("Published", style="dim")
        version_table.add_column("Downloads", justify="right")

        for ver in version_list:
            # Format date
            date_str = ver.published_at[:10] if ver.published_at else ""
            version_table.add_row(
                ver.version,
                date_str,
                format_install_count(ver.download_count),
            )

        console.print(version_table)


# =============================================================================
# Install & Manage Commands
# =============================================================================


@marketplace.command()
@click.argument("asset_ref")
@click.option(
    "--org", "-o",
    type=str,
    help="Organization ID for team install",
)
@click.option(
    "--pin",
    is_flag=True,
    help="Pin to the installed version (don't auto-update)",
)
@click.option(
    "--no-claude-config",
    is_flag=True,
    help="Skip automatic Claude Desktop/Code configuration",
)
def install(asset_ref: str, org: str | None, pin: bool, no_claude_config: bool):
    """Install an asset from the marketplace.

    \b
    ASSET_REF: Asset reference (e.g., @publisher/slug or @publisher/slug@1.0.0)

    \b
    Examples:
      repotoire marketplace install @repotoire/review-pr
      repotoire marketplace install @user/my-skill@2.0.0 --pin
      repotoire marketplace install @acme/team-tool --org org_123
      repotoire marketplace install @user/tool --no-claude-config

    \b
    Claude Integration:
      By default, installed assets are automatically configured in Claude Desktop/Code:
      - Commands are added to ~/.claude/commands/
      - Skills are added as MCP servers in ~/.claude.json
      - Hooks are added to ~/.claude/settings.json

      Use --no-claude-config to skip this automatic configuration.
    """
    try:
        publisher, slug, version = parse_asset_reference(asset_ref)
    except ValueError as e:
        raise click.ClickException(str(e))

    client = _get_client()
    full_name = f"@{publisher}/{slug}"

    console.print(f"\nInstalling {full_name}...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        try:
            # Step 1: Call install API
            task = progress.add_task("Checking limits...", total=None)
            result = client.install(publisher, slug, version, org)
            progress.update(task, description="✓ Checked limits")

            # Step 2: Download content
            progress.update(task, description="Downloading...")
            content = client.download_asset(result.download_url)
            progress.update(task, description="✓ Downloaded")

            # Step 3: Extract to local directory
            progress.update(task, description="Extracting...")
            ensure_directories_exist()
            local_path = extract_asset(
                publisher_slug=publisher,
                slug=slug,
                asset_type=result.asset.asset_type,
                content=content,
            )
            progress.update(task, description="✓ Extracted")

            # Step 4: Update manifest
            progress.update(task, description="Updating manifest...")
            update_manifest(
                full_name=full_name,
                version=result.version,
                asset_type=result.asset.asset_type,
                publisher_slug=publisher,
                name=result.asset.name,
                local_path=local_path,
                pinned=pin or bool(version),  # Pin if specific version requested
            )
            progress.update(task, description="✓ Updated manifest")

            # Step 5: Configure Claude (unless skipped)
            claude_configured = False
            if not no_claude_config:
                progress.update(task, description="Configuring Claude...")
                try:
                    claude_manager = _get_claude_manager()
                    claude_manager.sync_asset(
                        asset_type=result.asset.asset_type,
                        publisher_slug=publisher,
                        slug=slug,
                        version=result.version,
                        local_path=Path(str(local_path)),
                    )
                    claude_configured = True
                    progress.update(task, description="✓ Configured Claude")
                except ClaudeConfigError as e:
                    logger.warning(f"Failed to configure Claude: {e}")
                    progress.update(task, description="⚠ Claude config skipped")

        except TierLimitError as e:
            raise click.ClickException(
                f"Installation limit reached.\n"
                f"{e.message}\n"
                f"Upgrade at: https://repotoire.com/pricing"
            )
        except AssetNotFoundError:
            raise click.ClickException(
                f"Asset {full_name} not found.\n"
                f"Search for similar: repotoire marketplace search '{slug}'"
            )
        except MarketplaceAPIError as e:
            raise click.ClickException(f"Installation failed: {e.message}")
        except ValueError as e:
            raise click.ClickException(f"Extraction failed: {e}")

    # Track install event (fire-and-forget)
    client.track_event(
        publisher=publisher,
        slug=slug,
        event_type="install",
        version=result.version,
    )

    # Success message
    success_lines = [
        f"[green]✓ Installed {full_name}[/green]",
        f"  Version: {result.version}",
        f"  Location: {local_path}",
    ]

    if claude_configured:
        if result.asset.asset_type == "command":
            success_lines.append(f"  Claude: Added /{slug} command")
        elif result.asset.asset_type == "skill":
            success_lines.append(f"  Claude: Added repotoire-{slug} MCP server")
        elif result.asset.asset_type == "hook":
            success_lines.append(f"  Claude: Added hook configuration")

        success_lines.append("")
        success_lines.append("[dim]Restart Claude Desktop/Code to use the new asset[/dim]")

    console.print(Panel.fit(
        "\n".join(success_lines),
        border_style="green",
    ))


@marketplace.command()
@click.argument("asset_ref")
@click.option(
    "--org", "-o",
    type=str,
    help="Organization ID for team uninstall",
)
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--no-claude-config",
    is_flag=True,
    help="Skip removing from Claude Desktop/Code configuration",
)
def uninstall(asset_ref: str, org: str | None, force: bool, no_claude_config: bool):
    """Uninstall a marketplace asset.

    \b
    ASSET_REF: Asset reference (e.g., @publisher/slug)

    \b
    Examples:
      repotoire marketplace uninstall @repotoire/review-pr
      repotoire marketplace uninstall @user/my-skill --force
      repotoire marketplace uninstall @user/tool --no-claude-config

    \b
    Claude Integration:
      By default, uninstalling an asset also removes it from Claude Desktop/Code:
      - Commands are removed from ~/.claude/commands/
      - Skills are removed from MCP servers in ~/.claude.json
      - Hooks are removed from ~/.claude/settings.json

      Use --no-claude-config to keep the Claude configuration.
    """
    try:
        publisher, slug, _ = parse_asset_reference(asset_ref)
    except ValueError as e:
        raise click.ClickException(str(e))

    full_name = f"@{publisher}/{slug}"

    # Check if installed locally
    manifest = get_local_manifest()
    installed = manifest.get_asset(full_name)

    if not installed:
        raise click.ClickException(
            f"Asset {full_name} is not installed.\n"
            "Run 'repotoire marketplace list' to see installed assets."
        )

    # Confirm uninstall
    if not force:
        if not click.confirm(f"Uninstall {full_name}?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    client = _get_client()

    with console.status(f"Uninstalling {full_name}..."):
        try:
            # Call API to uninstall (removes from user's install list)
            client.uninstall(publisher, slug, org)
        except MarketplaceAPIError as e:
            # Log but continue - still remove local files
            logger.warning(f"API uninstall failed: {e.message}")

        # Remove local files
        remove_asset_files(publisher, slug, installed.asset_type)

        # Remove from manifest
        remove_from_manifest(full_name)

        # Remove from Claude config
        claude_removed = False
        if not no_claude_config:
            try:
                claude_manager = _get_claude_manager()
                claude_manager.unsync_asset(
                    asset_type=installed.asset_type,
                    publisher_slug=publisher,
                    slug=slug,
                )
                claude_removed = True
            except ClaudeConfigError as e:
                logger.warning(f"Failed to remove from Claude config: {e}")

    # Track uninstall event (fire-and-forget)
    client.track_event(
        publisher=publisher,
        slug=slug,
        event_type="uninstall",
        version=installed.version,
    )

    success_msg = f"[green]✓ Uninstalled {full_name}[/green]"
    if claude_removed:
        success_msg += "\n[dim]Removed from Claude Desktop/Code configuration[/dim]"

    console.print(success_msg)


@marketplace.command("list")
@click.option(
    "--type", "-t",
    type=click.Choice(["command", "skill", "style", "hook", "prompt"]),
    help="Filter by asset type",
)
def list_installed(type: str | None):
    """List installed marketplace assets.

    \b
    Examples:
      repotoire marketplace list
      repotoire marketplace list --type=command
    """
    manifest = get_local_manifest()

    assets = list(manifest.assets.items())

    if type:
        assets = [(name, a) for name, a in assets if a.asset_type == type]

    if not assets:
        if type:
            console.print(f"[yellow]No {type}s installed[/yellow]")
        else:
            console.print("[yellow]No marketplace assets installed[/yellow]")
            console.print("[dim]Try: repotoire marketplace browse[/dim]")
        return

    table = Table(
        title="Installed Assets",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )

    table.add_column("Asset", style="white")
    table.add_column("Type", style="dim")
    table.add_column("Version", style="cyan")
    table.add_column("Pinned", justify="center")

    for name, asset in sorted(assets):
        pinned_icon = "📌" if asset.pinned else ""
        table.add_row(
            name,
            asset.asset_type,
            asset.version,
            pinned_icon,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(assets)} asset(s)[/dim]")


@marketplace.command()
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Force re-download all assets (ignore cache)",
)
def sync(force: bool):
    """Sync installed assets with the marketplace.

    Downloads updates for installed assets and ensures local files are current.

    \b
    Examples:
      repotoire marketplace sync
      repotoire marketplace sync --force
    """
    client = _get_client()
    manifest = get_local_manifest()

    if not manifest.assets:
        console.print("[yellow]No assets to sync[/yellow]")
        console.print("[dim]Install assets with: repotoire marketplace install @publisher/slug[/dim]")
        return

    result = SyncResult(
        updated=[],
        unchanged=[],
        failed=[],
        removed=[],
    )

    console.print(f"\nSyncing {len(manifest.assets)} asset(s)...\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Syncing...", total=len(manifest.assets))

        for full_name, installed in manifest.assets.items():
            try:
                publisher, slug, _ = parse_asset_reference(full_name)
            except ValueError:
                result.failed.append((full_name, "Invalid asset reference"))
                progress.advance(task)
                continue

            progress.update(task, description=f"Checking {full_name}...")

            try:
                # Get latest asset info
                asset = client.get_asset(publisher, slug)

                # Check if update needed
                needs_update = False
                if force:
                    needs_update = True
                elif not installed.pinned and asset.latest_version != installed.version:
                    needs_update = True

                if needs_update:
                    # Download and extract
                    install_result = client.install(publisher, slug, None, None)
                    content = client.download_asset(install_result.download_url)

                    ensure_directories_exist()
                    local_path = extract_asset(
                        publisher_slug=publisher,
                        slug=slug,
                        asset_type=asset.asset_type,
                        content=content,
                    )

                    # Update manifest
                    update_manifest(
                        full_name=full_name,
                        version=install_result.version,
                        asset_type=asset.asset_type,
                        publisher_slug=publisher,
                        name=asset.name,
                        local_path=local_path,
                        pinned=installed.pinned,
                    )

                    # Track update event (fire-and-forget)
                    if asset.latest_version != installed.version:
                        client.track_event(
                            publisher=publisher,
                            slug=slug,
                            event_type="update",
                            version=install_result.version,
                            metadata={"from_version": installed.version},
                        )
                        result.updated.append(f"{full_name} ({installed.version} → {install_result.version})")
                    else:
                        result.updated.append(full_name)
                else:
                    result.unchanged.append(full_name)

            except AssetNotFoundError:
                result.failed.append((full_name, "Asset no longer available"))
            except MarketplaceAPIError as e:
                result.failed.append((full_name, e.message))
            except Exception as e:
                result.failed.append((full_name, str(e)))

            progress.advance(task)

    # Update sync time
    manifest.update_sync_time()
    manifest.save()

    # Show results
    console.print()

    for name in result.updated:
        if "→" in name:
            console.print(f"  ✓ {name.split(' (')[0]} [cyan](updated {name.split('(')[1]}[/cyan]")
        else:
            console.print(f"  ✓ {name} [dim](re-downloaded)[/dim]")

    for name in result.unchanged:
        console.print(f"  ✓ {name} [dim](up to date)[/dim]")

    for name, error in result.failed:
        console.print(f"  ✗ {name} [red]({error})[/red]")

    # Summary
    console.print()
    summary_parts = []
    if result.updated:
        summary_parts.append(f"{len(result.updated)} updated")
    if result.unchanged:
        summary_parts.append(f"{len(result.unchanged)} unchanged")
    if result.failed:
        summary_parts.append(f"[red]{len(result.failed)} failed[/red]")

    console.print(f"[green]✓ Synced {result.total_synced} asset(s)[/green] ({', '.join(summary_parts)})")


# =============================================================================
# Publish Commands
# =============================================================================


@marketplace.command()
@click.argument("asset_ref")
@click.argument("content_path", type=click.Path(exists=True))
@click.argument("version")
@click.option(
    "--name", "-n",
    type=str,
    help="Asset display name (required for new assets)",
)
@click.option(
    "--description", "-d",
    type=str,
    help="Asset description (required for new assets)",
)
@click.option(
    "--type", "-t",
    type=click.Choice(["command", "skill", "style", "hook", "prompt"]),
    help="Asset type (required for new assets)",
)
@click.option(
    "--changelog", "-c",
    type=str,
    help="Changelog for this version",
)
def publish(
    asset_ref: str,
    content_path: str,
    version: str,
    name: str | None,
    description: str | None,
    type: str | None,
    changelog: str | None,
):
    """Publish a new version of an asset to the marketplace.

    \b
    ASSET_REF: Asset reference (e.g., @me/my-command)
    CONTENT_PATH: Path to content file (e.g., ./command.md or ./skill.json)
    VERSION: Semantic version (e.g., 1.0.0)

    \b
    Examples:
      repotoire marketplace publish @me/review-pr ./command.md 1.0.0
      repotoire marketplace publish @me/my-skill ./skill.json 2.0.0 --changelog "Added new tools"

    \b
    For new assets, provide --name, --description, and --type:
      repotoire marketplace publish @me/new-cmd ./cmd.md 1.0.0 \\
        --name "My Command" --description "Does useful things" --type command
    """
    try:
        publisher, slug, _ = parse_asset_reference(asset_ref)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Read content file
    content_file = Path(content_path)
    try:
        content_text = content_file.read_text()

        # Parse content based on file type
        if content_file.suffix == ".json":
            content = json.loads(content_text)
        elif content_file.suffix == ".md":
            # For markdown files, wrap as command/prompt content
            content = {"prompt": content_text}
        else:
            # Try to parse as JSON, fall back to prompt
            try:
                content = json.loads(content_text)
            except json.JSONDecodeError:
                content = {"prompt": content_text}

    except IOError as e:
        raise click.ClickException(f"Failed to read content file: {e}")
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON in content file: {e}")

    client = _get_client()
    full_name = f"@{publisher}/{slug}"

    console.print(f"\nPublishing {full_name} version {version}...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        try:
            # Step 1: Validate content
            task = progress.add_task("Validating...", total=None)
            errors = client.validate_asset(type or "command", content)
            if errors:
                progress.stop()
                console.print("\n[red]Validation errors:[/red]")
                for error in errors:
                    console.print(f"  • {error}")
                raise click.ClickException("Content validation failed")
            progress.update(task, description="✓ Validated")

            # Step 2: Check/create publisher
            progress.update(task, description="Checking publisher...")
            my_publisher = client.get_my_publisher()
            if not my_publisher:
                raise click.ClickException(
                    "You don't have a publisher profile.\n"
                    "Create one at: https://repotoire.com/marketplace/publish"
                )
            if my_publisher.slug != publisher:
                raise click.ClickException(
                    f"Publisher @{publisher} doesn't match your profile @{my_publisher.slug}.\n"
                    f"Use: repotoire marketplace publish @{my_publisher.slug}/{slug} ..."
                )
            progress.update(task, description="✓ Publisher verified")

            # Step 3: Publish
            progress.update(task, description="Publishing...")
            result = client.publish_version(
                publisher=publisher,
                slug=slug,
                version=version,
                content=content,
                changelog=changelog,
                asset_type=type,
                name=name,
                description=description,
            )
            progress.update(task, description="✓ Published")

        except TierLimitError as e:
            raise click.ClickException(
                f"Publishing limit reached (3 assets on free tier).\n"
                f"Upgrade to Pro for unlimited publishing: https://repotoire.com/pricing"
            )
        except MarketplaceAPIError as e:
            raise click.ClickException(f"Publishing failed: {e.message}")

    # Success message
    marketplace_url = f"https://repotoire.com/marketplace/@{publisher}/{slug}"
    console.print(Panel.fit(
        f"[green]✓ Published {full_name} v{version}[/green]\n\n"
        f"View at: {marketplace_url}",
        border_style="green",
    ))


@marketplace.command()
@click.argument("asset_ref")
@click.option(
    "--pin/--unpin",
    default=None,
    help="Pin or unpin the installed version",
)
def config(asset_ref: str, pin: bool | None):
    """Configure an installed asset.

    \b
    ASSET_REF: Asset reference (e.g., @publisher/slug)

    \b
    Examples:
      repotoire marketplace config @repotoire/review-pr --pin
      repotoire marketplace config @user/my-skill --unpin
    """
    try:
        publisher, slug, _ = parse_asset_reference(asset_ref)
    except ValueError as e:
        raise click.ClickException(str(e))

    full_name = f"@{publisher}/{slug}"

    manifest = get_local_manifest()
    installed = manifest.get_asset(full_name)

    if not installed:
        raise click.ClickException(
            f"Asset {full_name} is not installed.\n"
            "Run 'repotoire marketplace list' to see installed assets."
        )

    if pin is None:
        # Show current config
        console.print(Panel.fit(
            f"[bold]Asset:[/bold] {full_name}\n"
            f"[bold]Version:[/bold] {installed.version}\n"
            f"[bold]Type:[/bold] {installed.asset_type}\n"
            f"[bold]Pinned:[/bold] {'Yes' if installed.pinned else 'No'}\n"
            f"[bold]Installed:[/bold] {installed.installed_at[:10]}\n"
            f"[bold]Location:[/bold] {installed.local_path or 'N/A'}",
            title="Asset Configuration",
            border_style="cyan",
        ))
        return

    # Update pin status
    installed.pinned = pin
    manifest.assets[full_name] = installed
    manifest.save()

    if pin:
        console.print(f"[green]✓ Pinned {full_name} to version {installed.version}[/green]")
        console.print("[dim]This version will not be updated during sync[/dim]")
    else:
        console.print(f"[green]✓ Unpinned {full_name}[/green]")
        console.print("[dim]This asset will be updated during sync[/dim]")


# =============================================================================
# Dependency & Update Commands
# =============================================================================


@marketplace.command()
@click.option(
    "--type", "-t",
    type=click.Choice(["command", "skill", "style", "hook", "prompt"]),
    help="Filter by asset type",
)
def outdated(type: str | None):
    """Check for outdated marketplace assets.

    Shows a table of installed assets with available updates.

    \b
    Examples:
      repotoire marketplace outdated
      repotoire marketplace outdated --type=skill
    """
    import asyncio
    from repotoire.marketplace import UpdateType, AssetUpdater

    client = _get_client()
    manifest = get_local_manifest()

    if not manifest.assets:
        console.print("[yellow]No assets installed[/yellow]")
        return

    # Get installed assets
    installed = {
        name: asset.version
        for name, asset in manifest.assets.items()
        if not asset.pinned and (type is None or asset.asset_type == type)
    }

    if not installed:
        console.print("[yellow]No unpinned assets to check[/yellow]")
        return

    console.print(f"\nChecking {len(installed)} asset(s) for updates...\n")

    async def check():
        updater = AssetUpdater(client)
        return await updater.check_updates(installed)

    with console.status("Checking for updates..."):
        updates = asyncio.run(check())

    if not updates:
        console.print("[green]All assets are up to date![/green]")
        return

    # Create table
    table = Table(
        title="Available Updates",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )

    table.add_column("Package", style="white")
    table.add_column("Current", style="dim")
    table.add_column("Latest", style="green")
    table.add_column("Type", justify="center")

    # Sort by update type (major first)
    updates.sort(key=lambda u: (u.update_type.value, u.slug))

    for update in updates:
        type_style = {
            UpdateType.MAJOR: "[red]major[/red]",
            UpdateType.MINOR: "[yellow]minor[/yellow]",
            UpdateType.PATCH: "[green]patch[/green]",
        }.get(update.update_type, str(update.update_type.value))

        table.add_row(
            update.slug,
            update.current,
            update.latest,
            type_style,
        )

    console.print(table)
    console.print(f"\n[dim]Found {len(updates)} update(s) available[/dim]")

    # Show major update warning
    major_updates = [u for u in updates if u.update_type == UpdateType.MAJOR]
    if major_updates:
        console.print(
            f"\n[yellow]⚠️  {len(major_updates)} major update(s) require manual approval[/yellow]"
        )
        console.print("[dim]Run 'repotoire marketplace update --approve-major' to include them[/dim]")


@marketplace.command("update")
@click.argument("asset_ref", required=False)
@click.option(
    "--approve-major",
    is_flag=True,
    help="Auto-approve major version updates",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be updated without making changes",
)
def update_assets(asset_ref: str | None, approve_major: bool, dry_run: bool):
    """Update installed marketplace assets.

    By default, only minor and patch updates are applied.
    Major updates require --approve-major flag.

    \b
    Examples:
      repotoire marketplace update
      repotoire marketplace update @creator/helper-skill
      repotoire marketplace update --approve-major
      repotoire marketplace update --dry-run
    """
    import asyncio
    from repotoire.marketplace import UpdateType, AssetUpdater

    client = _get_client()
    manifest = get_local_manifest()

    if not manifest.assets:
        console.print("[yellow]No assets installed[/yellow]")
        return

    # Filter by specific asset if provided
    if asset_ref:
        try:
            publisher, slug, version = parse_asset_reference(asset_ref)
        except ValueError as e:
            raise click.ClickException(str(e))

        full_name = f"@{publisher}/{slug}"
        asset = manifest.get_asset(full_name)

        if not asset:
            raise click.ClickException(f"Asset {full_name} is not installed")

        installed = {full_name: asset.version}
    else:
        # Get all unpinned assets
        installed = {
            name: asset.version
            for name, asset in manifest.assets.items()
            if not asset.pinned
        }

    if not installed:
        console.print("[yellow]No unpinned assets to update[/yellow]")
        return

    async def update():
        updater = AssetUpdater(client)
        updates = await updater.check_updates(installed)

        if dry_run:
            return updates, []

        return updates, await updater.apply_updates(updates, auto_approve=approve_major)

    with console.status("Checking for updates..."):
        updates, results = asyncio.run(update())

    if not updates:
        console.print("[green]All assets are up to date![/green]")
        return

    if dry_run:
        console.print("\n[bold]Dry run - would update:[/bold]\n")
        for update in updates:
            if update.update_type == UpdateType.MAJOR and not approve_major:
                console.print(
                    f"  [yellow]⏭ {update.slug}[/yellow] "
                    f"[dim]{update.current} → {update.latest} (major, skipped)[/dim]"
                )
            else:
                console.print(
                    f"  [green]✓ {update.slug}[/green] "
                    f"[dim]{update.current} → {update.latest}[/dim]"
                )
        return

    # Show results
    console.print()
    for result in results:
        if result.success:
            console.print(
                f"  [green]✓ {result.slug}[/green] "
                f"[dim]{result.old_version} → {result.new_version}[/dim]"
            )

            # Update manifest
            asset = manifest.get_asset(result.slug)
            if asset:
                asset.version = result.new_version
                manifest.assets[result.slug] = asset

            # Track update event (fire-and-forget)
            try:
                pub, slg, _ = parse_asset_reference(result.slug)
                client.track_event(
                    publisher=pub,
                    slug=slg,
                    event_type="update",
                    version=result.new_version,
                    metadata={"from_version": result.old_version},
                )
            except ValueError:
                pass  # Invalid reference, skip tracking
        else:
            console.print(
                f"  [red]✗ {result.slug}[/red] "
                f"[dim]{result.error}[/dim]"
            )

    # Save manifest
    manifest.save()

    # Summary
    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if not r.success)

    console.print()
    if success_count:
        console.print(f"[green]✓ Updated {success_count} asset(s)[/green]")
    if fail_count:
        console.print(f"[red]✗ Failed {fail_count} asset(s)[/red]")


@marketplace.command()
@click.option(
    "--depth", "-d",
    type=int,
    default=10,
    help="Maximum depth to display (default: 10)",
)
def tree(depth: int):
    """Display dependency tree for installed assets.

    Shows the complete dependency graph with versions.

    \b
    Examples:
      repotoire marketplace tree
      repotoire marketplace tree --depth=3

    \b
    Output example:
      @repotoire/code-review-workflow@2.1.0
      ├── @repotoire/security-scanner@1.3.0
      │   └── @repotoire/base-prompts@1.0.0
      └── @myorg/style-guide@2.1.5
    """
    from rich.tree import Tree

    manifest = get_local_manifest()

    if not manifest.assets:
        console.print("[yellow]No assets installed[/yellow]")
        return

    # Build tree structure
    root = Tree("[bold]Installed Assets[/bold]")

    # Group by type
    by_type: dict[str, list[tuple[str, Any]]] = {}
    for name, asset in sorted(manifest.assets.items()):
        asset_type = asset.asset_type
        if asset_type not in by_type:
            by_type[asset_type] = []
        by_type[asset_type].append((name, asset))

    for asset_type, assets in sorted(by_type.items()):
        type_node = root.add(f"[bold cyan]{asset_type}s[/bold cyan]")

        for name, asset in assets:
            version_str = f"@{asset.version}"
            pin_icon = " [dim]📌[/dim]" if asset.pinned else ""

            asset_node = type_node.add(
                f"[white]{name}[/white][dim]{version_str}[/dim]{pin_icon}"
            )

            # TODO: When we have dependency info in the manifest, show it here
            # For now, we don't have transitive dependency tracking in the manifest
            # This would require storing the resolved dependencies during install

    console.print(root)
    console.print(f"\n[dim]Total: {len(manifest.assets)} asset(s)[/dim]")


@marketplace.command()
@click.argument("asset_ref")
def pin(asset_ref: str):
    """Pin an asset to its current version.

    Pinned assets will not be updated during sync or update.

    \b
    ASSET_REF: Asset reference (e.g., @publisher/slug or @publisher/slug@1.0.0)

    \b
    Examples:
      repotoire marketplace pin @creator/helper-skill
      repotoire marketplace pin @creator/helper-skill@2.0.0
    """
    try:
        publisher, slug, version = parse_asset_reference(asset_ref)
    except ValueError as e:
        raise click.ClickException(str(e))

    full_name = f"@{publisher}/{slug}"

    manifest = get_local_manifest()
    installed = manifest.get_asset(full_name)

    if not installed:
        raise click.ClickException(
            f"Asset {full_name} is not installed.\n"
            "Run 'repotoire marketplace list' to see installed assets."
        )

    # If version specified and different, warn but still pin
    if version and version != installed.version:
        console.print(
            f"[yellow]Note: Installed version is {installed.version}, "
            f"not {version}. Pinning to installed version.[/yellow]"
        )

    installed.pinned = True
    manifest.assets[full_name] = installed
    manifest.save()

    console.print(f"[green]✓ Pinned {full_name} to version {installed.version}[/green]")
    console.print("[dim]This version will not be updated during sync[/dim]")


@marketplace.command()
@click.argument("asset_ref")
def unpin(asset_ref: str):
    """Unpin an asset to allow updates.

    Unpinned assets will receive updates during sync.

    \b
    ASSET_REF: Asset reference (e.g., @publisher/slug)

    \b
    Examples:
      repotoire marketplace unpin @creator/helper-skill
    """
    try:
        publisher, slug, _ = parse_asset_reference(asset_ref)
    except ValueError as e:
        raise click.ClickException(str(e))

    full_name = f"@{publisher}/{slug}"

    manifest = get_local_manifest()
    installed = manifest.get_asset(full_name)

    if not installed:
        raise click.ClickException(
            f"Asset {full_name} is not installed.\n"
            "Run 'repotoire marketplace list' to see installed assets."
        )

    if not installed.pinned:
        console.print(f"[yellow]Asset {full_name} is already unpinned[/yellow]")
        return

    installed.pinned = False
    manifest.assets[full_name] = installed
    manifest.save()

    console.print(f"[green]✓ Unpinned {full_name}[/green]")
    console.print("[dim]This asset will be updated during sync[/dim]")


# =============================================================================
# Claude Integration Commands
# =============================================================================


@marketplace.command("sync-claude")
def sync_claude():
    """Sync all installed assets to Claude Desktop/Code configuration.

    This command ensures all installed marketplace assets are properly
    configured in Claude Desktop/Code:

    - Commands are added to ~/.claude/commands/
    - Skills are added as MCP servers in ~/.claude.json
    - Hooks are added to ~/.claude/settings.json

    \b
    Examples:
      repotoire marketplace sync-claude
    """
    manifest = get_local_manifest()

    if not manifest.assets:
        console.print("[yellow]No marketplace assets installed[/yellow]")
        console.print("[dim]Install assets with: repotoire marketplace install @publisher/slug[/dim]")
        return

    console.print(f"\nSyncing {len(manifest.assets)} asset(s) to Claude configuration...\n")

    try:
        claude_manager = _get_claude_manager()
    except ClaudeConfigError as e:
        raise click.ClickException(f"Failed to initialize Claude config manager: {e}")

    synced = 0
    failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Syncing...", total=len(manifest.assets))

        for full_name, installed in manifest.assets.items():
            try:
                publisher, slug, _ = parse_asset_reference(full_name)
            except ValueError:
                failed += 1
                progress.advance(task)
                continue

            progress.update(task, description=f"Syncing {full_name}...")

            try:
                local_path = Path(installed.local_path) if installed.local_path else get_asset_path(
                    publisher, slug, installed.asset_type
                )

                claude_manager.sync_asset(
                    asset_type=installed.asset_type,
                    publisher_slug=publisher,
                    slug=slug,
                    version=installed.version,
                    local_path=local_path,
                )
                synced += 1

            except ClaudeConfigError as e:
                logger.warning(f"Failed to sync {full_name}: {e}")
                failed += 1

            progress.advance(task)

    # Summary
    console.print()
    if synced > 0:
        console.print(f"[green]✓ Synced {synced} asset(s) to Claude configuration[/green]")
    if failed > 0:
        console.print(f"[yellow]⚠ Failed to sync {failed} asset(s)[/yellow]")

    if synced > 0:
        console.print()
        console.print("[dim]Restart Claude Desktop/Code to apply changes[/dim]")


@marketplace.command("claude-status")
def claude_status():
    """Show Claude Desktop/Code configuration status.

    Displays information about:
    - Claude config file locations
    - Installed MCP servers
    - Installed slash commands
    - Configured hooks

    \b
    Examples:
      repotoire marketplace claude-status
    """
    try:
        claude_manager = _get_claude_manager()
    except ClaudeConfigError as e:
        raise click.ClickException(f"Failed to initialize Claude config manager: {e}")

    status = claude_manager.get_config_status()

    # Build status panel
    lines = [
        "[bold]Claude Configuration Status[/bold]",
        "",
        f"[bold]Config Files:[/bold]",
        f"  Main config: {status['config_path']}",
        f"  Settings: {status['settings_path']}",
        f"  Commands dir: {status['commands_dir']}",
        "",
    ]

    # MCP Servers
    lines.append(f"[bold]MCP Servers:[/bold] {status['mcp_servers_count']}")
    if status['mcp_servers']:
        for server in status['mcp_servers']:
            lines.append(f"  • {server}")
    else:
        lines.append("  [dim]No MCP servers configured[/dim]")
    lines.append("")

    # Commands
    lines.append(f"[bold]Slash Commands:[/bold] {status['commands_count']}")
    if status['commands']:
        for cmd in status['commands']:
            lines.append(f"  • /{cmd}")
    else:
        lines.append("  [dim]No slash commands installed[/dim]")
    lines.append("")

    # Hooks
    lines.append(f"[bold]Hooks:[/bold] {status['hooks_count']}")
    if status['hooks']:
        for hook in status['hooks']:
            lines.append(f"  • {hook}")
    else:
        lines.append("  [dim]No hooks configured[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title="Claude Integration",
        border_style="cyan",
        box=box.ROUNDED,
    ))


@marketplace.command("export")
@click.option(
    "--type", "-t",
    type=click.Choice(["command", "skill", "style", "hook", "prompt"]),
    help="Filter by asset type",
)
@click.option(
    "--format", "-f",
    type=click.Choice(["project", "artifact", "snippet"]),
    default="project",
    help="Export format (default: project)",
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    help="Output file path (default: stdout)",
)
@click.option(
    "--copy",
    is_flag=True,
    help="Copy output to clipboard",
)
def export_assets(type: str | None, format: str, output: str | None, copy: bool):
    """Export installed assets for use in Claude.ai.

    This command generates formatted instructions that can be copied
    into a Claude.ai Project's custom instructions.

    \b
    Export Formats:
      project   - Full project instructions with all assets (default)
      artifact  - Claude Artifact format for sharing
      snippet   - Compact snippet for quick use

    \b
    Examples:
      repotoire marketplace export
      repotoire marketplace export --type=style
      repotoire marketplace export --format=artifact --output=export.md
      repotoire marketplace export --copy

    \b
    Usage in Claude.ai:
      1. Run 'repotoire marketplace export --copy'
      2. Open Claude.ai and create or edit a Project
      3. Paste into the Project Instructions section
    """
    manifest = get_local_manifest()

    if not manifest.assets:
        console.print("[yellow]No marketplace assets installed[/yellow]")
        console.print("[dim]Install assets with: repotoire marketplace install @publisher/slug[/dim]")
        return

    # Build list of exported assets
    exported_assets: list[ExportedAsset] = []

    for full_name, installed in manifest.assets.items():
        # Filter by type if specified
        if type and installed.asset_type != type:
            continue

        try:
            publisher, slug, _ = parse_asset_reference(full_name)
        except ValueError:
            continue

        # Load asset content
        local_path = Path(installed.local_path) if installed.local_path else get_asset_path(
            publisher, slug, installed.asset_type
        )

        content: dict[str, Any] | str = ""
        if local_path.exists():
            if local_path.is_file():
                content = local_path.read_text()
            else:
                # Try to load manifest.json or main file
                manifest_file = local_path / "manifest.json"
                if manifest_file.exists():
                    try:
                        with open(manifest_file, "r") as f:
                            content = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        content = {}

        exported_assets.append(ExportedAsset(
            name=installed.name or slug,
            slug=slug,
            publisher=publisher,
            version=installed.version,
            asset_type=installed.asset_type,
            description=f"Marketplace asset @{publisher}/{slug}",
            content=content,
        ))

    if not exported_assets:
        if type:
            console.print(f"[yellow]No {type} assets installed[/yellow]")
        else:
            console.print("[yellow]No assets to export[/yellow]")
        return

    # Generate export
    export_text = generate_clipboard_text(exported_assets, format)

    # Handle output
    if copy:
        try:
            import pyperclip
            pyperclip.copy(export_text)
            console.print(f"[green]✓ Copied {len(exported_assets)} asset(s) to clipboard[/green]")
            console.print("[dim]Paste into Claude.ai Project Instructions[/dim]")
        except ImportError:
            console.print("[yellow]⚠ pyperclip not installed. Install with: pip install pyperclip[/yellow]")
            console.print()
            console.print(export_text)

    elif output:
        output_path = Path(output)
        output_path.write_text(export_text)
        console.print(f"[green]✓ Exported {len(exported_assets)} asset(s) to {output}[/green]")

    else:
        console.print(export_text)
