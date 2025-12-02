"""CLI commands for sandbox metrics and cost tracking.

This module provides CLI commands for viewing sandbox execution metrics,
cost summaries, and operational statistics.

Usage:
    repotoire sandbox-stats              # Show summary
    repotoire sandbox-stats --period 7   # Last 7 days
    repotoire sandbox-stats --by-type    # Breakdown by operation type
    repotoire sandbox-stats --slow       # Show slow operations
    repotoire sandbox-stats --failures   # Show recent failures
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from repotoire.sandbox.metrics import (
    SandboxMetricsCollector,
    CPU_RATE_PER_SECOND,
    MEMORY_RATE_PER_GB_SECOND,
)

console = Console()


def format_cost(cost: float) -> str:
    """Format cost for display."""
    if cost < 0.01:
        return f"${cost:.6f}"
    elif cost < 1.0:
        return f"${cost:.4f}"
    else:
        return f"${cost:.2f}"


def format_duration(ms: float) -> str:
    """Format duration for display."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    elif ms < 60000:
        return f"{ms/1000:.1f}s"
    else:
        return f"{ms/60000:.1f}m"


def format_percentage(value: float) -> str:
    """Format percentage with color."""
    return f"{value:.1f}%"


@click.command("sandbox-stats")
@click.option(
    "--period",
    "-p",
    type=int,
    default=30,
    help="Number of days to look back (default: 30)",
)
@click.option(
    "--customer-id",
    "-c",
    type=str,
    default=None,
    help="Filter by customer ID (admin only)",
)
@click.option(
    "--by-type",
    is_flag=True,
    help="Show breakdown by operation type",
)
@click.option(
    "--slow",
    is_flag=True,
    help="Show slow operations (>10s)",
)
@click.option(
    "--failures",
    is_flag=True,
    help="Show recent failures",
)
@click.option(
    "--top-customers",
    type=int,
    default=0,
    help="Show top N customers by cost (admin only)",
)
@click.option(
    "--json-output",
    is_flag=True,
    help="Output as JSON",
)
def sandbox_stats(
    period: int,
    customer_id: str | None,
    by_type: bool,
    slow: bool,
    failures: bool,
    top_customers: int,
    json_output: bool,
) -> None:
    """Show sandbox execution metrics and cost statistics.

    Displays comprehensive statistics about E2B sandbox operations including
    cost breakdown, success rates, and operational health metrics.

    Examples:

        # Show summary for last 30 days
        repotoire sandbox-stats

        # Show last 7 days with breakdown by operation type
        repotoire sandbox-stats --period 7 --by-type

        # Show slow operations
        repotoire sandbox-stats --slow

        # Show recent failures
        repotoire sandbox-stats --failures

        # Admin: Show top 10 customers by cost
        repotoire sandbox-stats --top-customers 10
    """
    asyncio.run(
        _sandbox_stats_async(
            period=period,
            customer_id=customer_id,
            by_type=by_type,
            slow=slow,
            failures=failures,
            top_customers=top_customers,
            json_output=json_output,
        )
    )


async def _sandbox_stats_async(
    period: int,
    customer_id: str | None,
    by_type: bool,
    slow: bool,
    failures: bool,
    top_customers: int,
    json_output: bool,
) -> None:
    """Async implementation of sandbox stats."""
    collector = SandboxMetricsCollector()

    try:
        await collector.connect()
    except Exception as e:
        console.print(f"[red]Failed to connect to metrics database: {e}[/red]")
        console.print("[dim]Make sure REPOTOIRE_TIMESCALE_URI is set correctly[/dim]")
        return

    try:
        start_date = datetime.now(timezone.utc) - timedelta(days=period)

        # Always show summary
        await _show_summary(collector, customer_id, start_date, period, json_output)

        # Optional sections
        if by_type:
            await _show_by_type(collector, customer_id, start_date, json_output)

        if slow:
            await _show_slow_operations(collector, customer_id, json_output)

        if failures:
            await _show_failures(collector, customer_id, json_output)

        if top_customers > 0:
            await _show_top_customers(collector, start_date, top_customers, json_output)

    finally:
        await collector.close()


async def _show_summary(
    collector: SandboxMetricsCollector,
    customer_id: str | None,
    start_date: datetime,
    period: int,
    json_output: bool,
) -> None:
    """Show summary statistics."""
    summary = await collector.get_cost_summary(
        customer_id=customer_id,
        start_date=start_date,
    )

    if json_output:
        import json
        console.print(json.dumps(summary, indent=2))
        return

    # Create summary panel
    title = f"Sandbox Metrics Summary (Last {period} days)"
    if customer_id:
        title += f" - Customer: {customer_id}"

    # Build summary text
    success_rate = summary.get("success_rate", 0)
    success_color = "green" if success_rate >= 95 else "yellow" if success_rate >= 80 else "red"

    summary_text = Text()
    summary_text.append(f"Total Operations:     ", style="bold")
    summary_text.append(f"{summary.get('total_operations', 0):,}\n")
    summary_text.append(f"Success Rate:         ", style="bold")
    summary_text.append(f"{success_rate:.1f}%\n", style=success_color)
    summary_text.append(f"Total Cost:           ", style="bold")
    summary_text.append(f"{format_cost(summary.get('total_cost_usd', 0))}\n")
    summary_text.append(f"Avg Duration:         ", style="bold")
    summary_text.append(f"{format_duration(summary.get('avg_duration_ms', 0))}\n")
    summary_text.append(f"Total CPU-seconds:    ", style="bold")
    summary_text.append(f"{summary.get('total_cpu_seconds', 0):,.1f}\n")
    summary_text.append(f"Total GB-seconds:     ", style="bold")
    summary_text.append(f"{summary.get('total_memory_gb_seconds', 0):,.1f}\n")

    panel = Panel(
        summary_text,
        title=title,
        border_style="blue",
        box=box.ROUNDED,
    )
    console.print(panel)
    console.print()


async def _show_by_type(
    collector: SandboxMetricsCollector,
    customer_id: str | None,
    start_date: datetime,
    json_output: bool,
) -> None:
    """Show breakdown by operation type."""
    breakdown = await collector.get_cost_by_operation_type(
        customer_id=customer_id,
        start_date=start_date,
    )

    if json_output:
        import json
        console.print(json.dumps(breakdown, indent=2))
        return

    if not breakdown:
        console.print("[yellow]No operations found[/yellow]")
        return

    table = Table(
        title="Cost by Operation Type",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )

    table.add_column("Operation Type", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Total Cost", justify="right")
    table.add_column("%", justify="right")
    table.add_column("Avg Duration", justify="right")
    table.add_column("Success Rate", justify="right")

    for item in breakdown:
        success_rate = item.get("success_rate", 0)
        success_style = "green" if success_rate >= 95 else "yellow" if success_rate >= 80 else "red"

        table.add_row(
            item.get("operation_type", "unknown"),
            f"{item.get('count', 0):,}",
            format_cost(item.get("total_cost_usd", 0)),
            f"{item.get('percentage', 0):.1f}%",
            format_duration(item.get("avg_duration_ms", 0)),
            Text(f"{success_rate:.1f}%", style=success_style),
        )

    console.print(table)
    console.print()


async def _show_slow_operations(
    collector: SandboxMetricsCollector,
    customer_id: str | None,
    json_output: bool,
) -> None:
    """Show slow operations."""
    slow_ops = await collector.get_slow_operations(
        threshold_ms=10000,
        limit=20,
        customer_id=customer_id,
    )

    if json_output:
        import json
        console.print(json.dumps(slow_ops, indent=2))
        return

    if not slow_ops:
        console.print("[green]No slow operations (>10s) found[/green]")
        return

    table = Table(
        title="Slow Operations (>10s)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold yellow",
    )

    table.add_column("Time", style="dim")
    table.add_column("Type")
    table.add_column("Duration", justify="right", style="bold")
    table.add_column("Cost", justify="right")
    table.add_column("Customer", style="dim")
    table.add_column("Status", justify="center")

    for op in slow_ops:
        time_str = op.get("time", "")[:16] if op.get("time") else ""
        status = "[green]OK[/green]" if op.get("success") else "[red]FAIL[/red]"

        table.add_row(
            time_str,
            op.get("operation_type", "unknown"),
            format_duration(op.get("duration_ms", 0)),
            format_cost(op.get("cost_usd", 0)),
            op.get("customer_id", "-") or "-",
            status,
        )

    console.print(table)
    console.print()


async def _show_failures(
    collector: SandboxMetricsCollector,
    customer_id: str | None,
    json_output: bool,
) -> None:
    """Show recent failures."""
    failures = await collector.get_recent_failures(
        limit=20,
        customer_id=customer_id,
    )

    if json_output:
        import json
        console.print(json.dumps(failures, indent=2))
        return

    if not failures:
        console.print("[green]No recent failures found[/green]")
        return

    table = Table(
        title="Recent Failures",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold red",
    )

    table.add_column("Time", style="dim")
    table.add_column("Type")
    table.add_column("Error", max_width=50)
    table.add_column("Duration", justify="right")
    table.add_column("Customer", style="dim")

    for failure in failures:
        time_str = failure.get("time", "")[:16] if failure.get("time") else ""
        error_msg = failure.get("error_message", "-") or "-"
        if len(error_msg) > 47:
            error_msg = error_msg[:47] + "..."

        table.add_row(
            time_str,
            failure.get("operation_type", "unknown"),
            error_msg,
            format_duration(failure.get("duration_ms", 0)),
            failure.get("customer_id", "-") or "-",
        )

    console.print(table)
    console.print()


async def _show_top_customers(
    collector: SandboxMetricsCollector,
    start_date: datetime,
    limit: int,
    json_output: bool,
) -> None:
    """Show top customers by cost."""
    customers = await collector.get_cost_by_customer(
        start_date=start_date,
        limit=limit,
    )

    if json_output:
        import json
        console.print(json.dumps(customers, indent=2))
        return

    if not customers:
        console.print("[yellow]No customer data found[/yellow]")
        return

    table = Table(
        title=f"Top {limit} Customers by Cost",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )

    table.add_column("Customer ID")
    table.add_column("Operations", justify="right")
    table.add_column("Total Cost", justify="right", style="bold")
    table.add_column("Avg Duration", justify="right")
    table.add_column("Success Rate", justify="right")

    for customer in customers:
        success_rate = customer.get("success_rate", 0)
        success_style = "green" if success_rate >= 95 else "yellow" if success_rate >= 80 else "red"

        table.add_row(
            customer.get("customer_id", "unknown"),
            f"{customer.get('total_operations', 0):,}",
            format_cost(customer.get("total_cost_usd", 0)),
            format_duration(customer.get("avg_duration_ms", 0)),
            Text(f"{success_rate:.1f}%", style=success_style),
        )

    console.print(table)
    console.print()


# E2B pricing reference for help text
PRICING_INFO = f"""
E2B Sandbox Pricing Reference:
  CPU:    ${CPU_RATE_PER_SECOND:.6f}/CPU-second
  Memory: ${MEMORY_RATE_PER_GB_SECOND:.7f}/GB-second

Example cost calculation (60s with 2 CPUs, 2GB RAM):
  CPU:    60 * 2 * ${CPU_RATE_PER_SECOND} = $0.00168
  Memory: 60 * 2 * ${MEMORY_RATE_PER_GB_SECOND} = $0.00030
  Total:  $0.00198
"""
