"""Graph database management CLI commands.

This module provides CLI commands for managing tenant graph storage
in FalkorDB, including provisioning, deprovisioning, and viewing statistics.

Usage:
    repotoire graph provision <org_id> --slug <slug>
    repotoire graph deprovision <org_id> --slug <slug> --confirm
    repotoire graph stats <org_id>
    repotoire graph list
"""

import asyncio
import os
from uuid import UUID

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from repotoire.graph.tenant_factory import GraphClientFactory, get_factory

console = Console()


@click.group()
def graph():
    """Graph database management commands for multi-tenancy."""
    pass


@graph.command()
@click.argument("org_id", type=str)
@click.option(
    "--slug",
    "-s",
    required=True,
    help="Organization slug for naming the graph",
)
def provision(org_id: str, slug: str):
    """Provision graph storage for an organization.

    Creates a new graph in FalkorDB for the specified organization.
    Note: FalkorDB graphs are created automatically on first use,
    so this command primarily validates the configuration.

    ORG_ID: UUID of the organization

    Example:
        repotoire graph provision 550e8400-e29b-41d4-a716-446655440000 --slug acme-corp
    """
    try:
        parsed_org_id = UUID(org_id)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid UUID format: {org_id}")
        raise click.Abort()

    factory = GraphClientFactory()

    with console.status(f"Provisioning graph for {slug}..."):
        graph_name = asyncio.run(factory.provision_tenant(parsed_org_id, slug))

    console.print(Panel.fit(
        f"[green]Graph provisioned successfully![/green]\n\n"
        f"Organization ID: {org_id}\n"
        f"Organization Slug: {slug}\n"
        f"Graph Name: [cyan]{graph_name}[/cyan]",
        title="Graph Provisioned",
        border_style="green",
    ))


@graph.command()
@click.argument("org_id", type=str)
@click.option(
    "--slug",
    "-s",
    required=True,
    help="Organization slug",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Confirm deletion without prompting",
)
def deprovision(org_id: str, slug: str, confirm: bool):
    """Remove graph storage for an organization.

    WARNING: This permanently deletes ALL graph data for the organization!

    ORG_ID: UUID of the organization

    Example:
        repotoire graph deprovision 550e8400-... --slug acme-corp --confirm
    """
    try:
        parsed_org_id = UUID(org_id)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid UUID format: {org_id}")
        raise click.Abort()

    if not confirm:
        console.print(Panel.fit(
            "[bold red]WARNING: This action is IRREVERSIBLE![/bold red]\n\n"
            f"Organization: {slug}\n"
            f"Organization ID: {org_id}\n\n"
            "All graph data for this organization will be permanently deleted.",
            title="Confirm Deletion",
            border_style="red",
        ))
        if not click.confirm("Are you sure you want to proceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            raise click.Abort()

    factory = GraphClientFactory()

    with console.status(f"Deprovisioning graph for {slug}..."):
        asyncio.run(factory.deprovision_tenant(parsed_org_id, slug))

    console.print(f"[green]Graph for {slug} has been deleted.[/green]")


@graph.command()
@click.argument("org_id", type=str)
@click.option(
    "--slug",
    "-s",
    help="Organization slug (optional, uses UUID prefix if not provided)",
)
def stats(org_id: str, slug: str | None):
    """Show graph statistics for an organization.

    ORG_ID: UUID of the organization

    Example:
        repotoire graph stats 550e8400-... --slug acme-corp
    """
    try:
        parsed_org_id = UUID(org_id)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid UUID format: {org_id}")
        raise click.Abort()

    factory = GraphClientFactory()

    try:
        with console.status("Connecting to graph..."):
            client = factory.get_client(parsed_org_id, slug)
            stats_data = client.get_stats()
    except Exception as e:
        console.print(f"[red]Error connecting to graph:[/red] {e}")
        raise click.Abort()

    # Generate graph name for display
    graph_name = factory._generate_graph_name(parsed_org_id, slug)

    table = Table(
        title=f"Graph Statistics: {graph_name}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Metric", style="white")
    table.add_column("Value", style="green", justify="right")

    for key, value in stats_data.items():
        # Format key: convert snake_case to Title Case
        display_key = key.replace("_", " ").title()
        table.add_row(display_key, f"{value:,}")

    console.print(table)

    # Show client info
    console.print(f"\n[dim]Host: {factory.falkordb_host}:{factory.falkordb_port}[/dim]")


@graph.command()
def list_cached():
    """List currently cached graph clients.

    Shows all organizations with active graph connections in the factory cache.

    Example:
        repotoire graph list-cached
    """
    factory = get_factory()

    cached_orgs = factory.get_cached_org_ids()

    if not cached_orgs:
        console.print("[yellow]No cached graph clients.[/yellow]")
        return

    table = Table(
        title="Cached Graph Clients",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Organization ID", style="white")
    table.add_column("Graph Name", style="green")

    for org_id in cached_orgs:
        graph_name = factory._generate_graph_name(org_id, None)
        table.add_row(str(org_id), graph_name)

    console.print(table)
    console.print(f"\n[dim]Total cached clients: {len(cached_orgs)}[/dim]")


@graph.command()
def close_all():
    """Close all cached graph clients.

    Releases all database connections held by the factory cache.
    Useful for cleanup or before reconfiguration.

    Example:
        repotoire graph close-all
    """
    factory = get_factory()

    count = len(factory.get_cached_org_ids())

    if count == 0:
        console.print("[yellow]No cached clients to close.[/yellow]")
        return

    factory.close_all()
    console.print(f"[green]Closed {count} cached graph client(s).[/green]")


@graph.command()
@click.argument("org_id", type=str)
@click.option(
    "--slug",
    "-s",
    help="Organization slug",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Confirm without prompting",
)
def clear(org_id: str, slug: str | None, confirm: bool):
    """Clear all data in an organization's graph.

    WARNING: This deletes all nodes and relationships in the graph!
    The graph itself remains, only the data is deleted.

    ORG_ID: UUID of the organization

    Example:
        repotoire graph clear 550e8400-... --slug acme-corp --confirm
    """
    try:
        parsed_org_id = UUID(org_id)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid UUID format: {org_id}")
        raise click.Abort()

    factory = GraphClientFactory()
    graph_name = factory._generate_graph_name(parsed_org_id, slug)

    if not confirm:
        console.print(Panel.fit(
            "[bold yellow]WARNING: This will delete all data![/bold yellow]\n\n"
            f"Graph: {graph_name}\n"
            f"Organization ID: {org_id}\n\n"
            "All nodes and relationships will be deleted.\n"
            "The graph itself will remain.",
            title="Confirm Clear",
            border_style="yellow",
        ))
        if not click.confirm("Are you sure you want to proceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            raise click.Abort()

    try:
        with console.status(f"Clearing graph {graph_name}..."):
            client = factory.get_client(parsed_org_id, slug)
            client.clear_graph()
    except Exception as e:
        console.print(f"[red]Error clearing graph:[/red] {e}")
        raise click.Abort()

    console.print(f"[green]Graph {graph_name} has been cleared.[/green]")


@graph.command()
def config():
    """Show current graph configuration.

    Displays environment variables and settings used for FalkorDB connections.

    Example:
        repotoire graph config
    """
    table = Table(
        title="FalkorDB Configuration",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Setting", style="white")
    table.add_column("Value", style="green")
    table.add_column("Source", style="dim")

    # FalkorDB settings - check both FALKORDB_* and REPOTOIRE_FALKORDB_* env vars
    # On Fly.io, default host is repotoire-falkor.internal
    is_fly = bool(os.environ.get("FLY_APP_NAME"))
    default_host = "repotoire-falkor.internal" if is_fly else "localhost"

    falkordb_host = os.environ.get(
        "FALKORDB_HOST",
        os.environ.get("REPOTOIRE_FALKORDB_HOST", default_host)
    )
    host_source = (
        "FALKORDB_HOST" if os.environ.get("FALKORDB_HOST")
        else "REPOTOIRE_FALKORDB_HOST" if os.environ.get("REPOTOIRE_FALKORDB_HOST")
        else "fly.io default" if is_fly
        else "default"
    )
    table.add_row("Host", falkordb_host, host_source)

    falkordb_port = os.environ.get(
        "FALKORDB_PORT",
        os.environ.get("REPOTOIRE_FALKORDB_PORT", "6379")
    )
    port_source = (
        "FALKORDB_PORT" if os.environ.get("FALKORDB_PORT")
        else "REPOTOIRE_FALKORDB_PORT" if os.environ.get("REPOTOIRE_FALKORDB_PORT")
        else "default"
    )
    table.add_row("Port", falkordb_port, port_source)

    has_falkordb_pw = os.environ.get("FALKORDB_PASSWORD") or os.environ.get("REPOTOIRE_FALKORDB_PASSWORD")
    pw_source = (
        "FALKORDB_PASSWORD" if os.environ.get("FALKORDB_PASSWORD")
        else "REPOTOIRE_FALKORDB_PASSWORD" if os.environ.get("REPOTOIRE_FALKORDB_PASSWORD")
        else "not set"
    )
    table.add_row(
        "Password",
        "***" if has_falkordb_pw else "(none)",
        pw_source,
    )

    # Show Fly.io environment status
    table.add_row(
        "Fly.io Environment",
        "Yes" if is_fly else "No",
        "FLY_APP_NAME" if is_fly else "not detected",
    )

    # Multi-tenancy info
    table.add_row(
        "Multi-tenancy",
        "Graph per tenant",
        "default",
    )

    console.print(table)
