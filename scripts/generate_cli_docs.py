#!/usr/bin/env python3
"""Generate CLI reference documentation from Click commands.

This script introspects the Repotoire CLI and generates comprehensive
markdown documentation for all commands, subcommands, and options.

Usage:
    python scripts/generate_cli_docs.py > docs/cli-reference.md
    python scripts/generate_cli_docs.py --output docs/cli/
"""

import click
import sys
from pathlib import Path
from typing import TextIO

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from repotoire.cli import cli


def get_command_tree(group: click.Group, prefix: str = "") -> list[tuple[str, click.Command]]:
    """Recursively get all commands in a Click group."""
    commands = []

    for name in sorted(group.commands.keys()):
        cmd = group.commands[name]
        full_name = f"{prefix} {name}".strip()
        commands.append((full_name, cmd))

        if isinstance(cmd, click.Group):
            commands.extend(get_command_tree(cmd, full_name))

    return commands


def format_option(param: click.Option) -> str:
    """Format a Click option for markdown."""
    # Build option string
    opts = ", ".join(param.opts)
    if param.secondary_opts:
        opts += ", " + ", ".join(param.secondary_opts)

    # Add type info
    type_str = ""
    if param.type and not param.is_flag:
        if isinstance(param.type, click.Choice):
            type_str = f" `[{'|'.join(param.type.choices)}]`"
        elif hasattr(param.type, 'name') and param.type.name != 'BOOL':
            type_str = f" `{param.type.name.upper()}`"

    # Add default
    default_str = ""
    if param.default is not None and param.default != () and not param.is_flag:
        if isinstance(param.default, bool):
            default_str = f" (default: {str(param.default).lower()})"
        else:
            default_str = f" (default: {param.default})"

    # Add required marker
    required_str = " **(required)**" if param.required else ""

    return f"`{opts}`{type_str}{required_str}{default_str}"


def format_argument(param: click.Argument) -> str:
    """Format a Click argument for markdown."""
    name = param.name.upper()
    required_str = "" if param.required else " (optional)"
    return f"`{name}`{required_str}"


def generate_command_doc(name: str, cmd: click.Command) -> str:
    """Generate markdown documentation for a single command."""
    lines = []

    # Command header
    depth = name.count(" ") + 2
    header = "#" * min(depth, 4)
    lines.append(f"{header} `repotoire {name}`")
    lines.append("")

    # Description
    if cmd.help:
        # Click uses \b to mark pre-formatted blocks
        help_text = cmd.help
        # Convert Click's \b markers to proper formatting
        sections = help_text.split("\n\n")
        for section in sections:
            if section.strip().startswith("\\b"):
                # Pre-formatted block - preserve spacing
                section = section.replace("\\b\n", "").replace("\\b", "")
                lines.append("```")
                lines.append(section.strip())
                lines.append("```")
            else:
                lines.append(section.strip())
            lines.append("")

    # Arguments
    arguments = [p for p in cmd.params if isinstance(p, click.Argument)]
    if arguments:
        lines.append("**Arguments:**")
        lines.append("")
        for arg in arguments:
            help_text = getattr(arg, 'help', '') or f"The {arg.name.replace('_', ' ')}"
            lines.append(f"- {format_argument(arg)} - {help_text}")
        lines.append("")

    # Options
    options = [p for p in cmd.params if isinstance(p, click.Option) and not p.hidden]
    if options:
        lines.append("**Options:**")
        lines.append("")
        lines.append("| Option | Description |")
        lines.append("|--------|-------------|")
        for opt in options:
            opt_str = format_option(opt)
            help_text = opt.help or ""
            # Escape pipes in help text
            help_text = help_text.replace("|", "\\|")
            lines.append(f"| {opt_str} | {help_text} |")
        lines.append("")

    # Environment variables
    env_vars = [(p, p.envvar) for p in cmd.params if isinstance(p, click.Option) and p.envvar]
    if env_vars:
        lines.append("**Environment Variables:**")
        lines.append("")
        for opt, envvar in env_vars:
            lines.append(f"- `{envvar}` - {opt.help or opt.name}")
        lines.append("")

    return "\n".join(lines)


def generate_toc(commands: list[tuple[str, click.Command]]) -> str:
    """Generate table of contents."""
    lines = ["## Table of Contents", ""]

    # Group commands
    current_group = None
    for name, cmd in commands:
        parts = name.split()
        if len(parts) == 1:
            # Top-level command
            anchor = name.replace(" ", "-").lower()
            lines.append(f"- [`repotoire {name}`](#{anchor})")
        elif len(parts) == 2 and parts[0] != current_group:
            # New group
            current_group = parts[0]
            anchor = parts[0].lower()
            lines.append(f"- [`repotoire {parts[0]}`](#{anchor})")

    lines.append("")
    return "\n".join(lines)


def generate_full_docs(output: TextIO | None = None) -> str:
    """Generate complete CLI reference documentation."""
    lines = []

    # Header
    lines.append("# Repotoire CLI Reference")
    lines.append("")
    lines.append("Complete reference for all Repotoire command-line interface commands.")
    lines.append("")
    lines.append("## Installation")
    lines.append("")
    lines.append("```bash")
    lines.append("pip install repotoire")
    lines.append("```")
    lines.append("")
    lines.append("## Quick Start")
    lines.append("")
    lines.append("```bash")
    lines.append("# Initialize configuration")
    lines.append("repotoire init")
    lines.append("")
    lines.append("# Ingest a codebase")
    lines.append("repotoire ingest ./my-project")
    lines.append("")
    lines.append("# Run analysis")
    lines.append("repotoire analyze ./my-project")
    lines.append("")
    lines.append("# Ask questions")
    lines.append('repotoire ask "Where is authentication handled?"')
    lines.append("```")
    lines.append("")

    # Global options
    lines.append("## Global Options")
    lines.append("")
    lines.append("These options apply to all commands:")
    lines.append("")
    lines.append("| Option | Description |")
    lines.append("|--------|-------------|")
    lines.append("| `--version` | Show version and exit |")
    lines.append("| `-c, --config PATH` | Path to config file (.reporc or falkor.toml) |")
    lines.append("| `--log-level LEVEL` | Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |")
    lines.append("| `--log-format FORMAT` | Log output format (json, human) |")
    lines.append("| `--log-file PATH` | Write logs to file |")
    lines.append("| `--help` | Show help message and exit |")
    lines.append("")

    # Get all commands
    commands = get_command_tree(cli)

    # Table of contents
    lines.append(generate_toc(commands))

    # Commands section
    lines.append("## Commands")
    lines.append("")

    # Generate docs for each command
    for name, cmd in commands:
        lines.append(generate_command_doc(name, cmd))
        lines.append("---")
        lines.append("")

    # Environment variables reference
    lines.append("## Environment Variables Reference")
    lines.append("")
    lines.append("| Variable | Description |")
    lines.append("|----------|-------------|")
    lines.append("| `FALKORDB_HOST` | FalkorDB host (e.g., bolt://localhost:7687) |")
    lines.append("| `FALKORDB_PASSWORD` | FalkorDB password |")
    lines.append("| `FALKORDB_USER` | FalkorDB user (default: neo4j) |")
    lines.append("| `REPOTOIRE_DB_TYPE` | Database type (falkordb) |")
    lines.append("| `REPOTOIRE_TIMESCALE_URI` | TimescaleDB connection string |")
    lines.append("| `REPOTOIRE_OFFLINE` | Run in offline mode (skip auth) |")
    lines.append("| `OPENAI_API_KEY` | OpenAI API key for embeddings/RAG |")
    lines.append("| `VOYAGE_API_KEY` | Voyage AI API key for code embeddings |")
    lines.append("| `DEEPINFRA_API_KEY` | DeepInfra API key for embeddings |")
    lines.append("| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |")
    lines.append("| `E2B_API_KEY` | E2B API key for sandbox execution |")
    lines.append("")

    # Configuration file reference
    lines.append("## Configuration File")
    lines.append("")
    lines.append("Repotoire looks for configuration in these locations (in order):")
    lines.append("")
    lines.append("1. Path specified with `--config`")
    lines.append("2. `.reporc` in current directory")
    lines.append("3. `falkor.toml` in current directory")
    lines.append("4. `~/.config/repotoire/config.toml`")
    lines.append("")
    lines.append("Example configuration:")
    lines.append("")
    lines.append("```toml")
    lines.append("[database]")
    lines.append('uri = "bolt://localhost:7687"')
    lines.append('user = "default"')
    lines.append('password = "${FALKORDB_PASSWORD}"  # Environment variable interpolation')
    lines.append("")
    lines.append("[ingestion]")
    lines.append('patterns = ["**/*.py", "**/*.js", "**/*.ts"]')
    lines.append("batch_size = 100")
    lines.append("max_file_size_mb = 10")
    lines.append("")
    lines.append("[embeddings]")
    lines.append('backend = "auto"')
    lines.append("")
    lines.append("[logging]")
    lines.append('level = "INFO"')
    lines.append('format = "human"')
    lines.append("```")
    lines.append("")

    content = "\n".join(lines)

    if output:
        output.write(content)

    return content


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate CLI documentation")
    parser.add_argument("--output", "-o", help="Output file or directory")
    parser.add_argument("--split", action="store_true", help="Split into separate files per command group")
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
        if args.split:
            # Split mode - create directory with multiple files
            output_path.mkdir(parents=True, exist_ok=True)

            # For now, just generate single file
            with open(output_path / "cli-reference.md", "w") as f:
                generate_full_docs(f)
            print(f"Generated {output_path / 'cli-reference.md'}")
        else:
            # Single file mode
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                generate_full_docs(f)
            print(f"Generated {output_path}")
    else:
        # Print to stdout
        print(generate_full_docs())


if __name__ == "__main__":
    main()
