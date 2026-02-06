"""Guided first-run setup command for Repotoire.

Provides an interactive initialization experience that:
1. Checks if already initialized
2. Offers optional cloud authentication
3. Detects project type and shows what will be analyzed
4. Creates .reporc config with sensible defaults
5. Runs first analysis
6. Shows next steps
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from repotoire.cli.errors import handle_errors
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()


@dataclass
class ProjectInfo:
    """Detected project information."""

    primary_language: str
    languages: list[str]
    has_pyproject: bool = False
    has_package_json: bool = False
    has_go_mod: bool = False
    has_cargo_toml: bool = False
    has_gemfile: bool = False
    has_requirements_txt: bool = False
    has_setup_py: bool = False
    has_tsconfig: bool = False
    file_count: dict[str, int] = None

    def __post_init__(self):
        if self.file_count is None:
            self.file_count = {}


def detect_project_type(repo_path: Path) -> ProjectInfo:
    """Detect project type from marker files and file extensions.

    Args:
        repo_path: Path to the repository

    Returns:
        ProjectInfo with detected languages and metadata
    """
    # Check for language marker files
    has_pyproject = (repo_path / "pyproject.toml").exists()
    has_package_json = (repo_path / "package.json").exists()
    has_go_mod = (repo_path / "go.mod").exists()
    has_cargo_toml = (repo_path / "Cargo.toml").exists()
    has_gemfile = (repo_path / "Gemfile").exists()
    has_requirements_txt = (repo_path / "requirements.txt").exists()
    has_setup_py = (repo_path / "setup.py").exists()
    has_tsconfig = (repo_path / "tsconfig.json").exists()

    # Count files by extension (quick scan, not recursive into node_modules etc)
    file_counts: dict[str, int] = {}
    excluded_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".tox",
        ".eggs",
    }

    extension_to_language = {
        ".py": "Python",
        ".pyi": "Python",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".kt": "Kotlin",
        ".rb": "Ruby",
        ".php": "PHP",
        ".cs": "C#",
        ".swift": "Swift",
        ".scala": "Scala",
        ".c": "C",
        ".cpp": "C++",
        ".cc": "C++",
        ".h": "C/C++",
        ".hpp": "C++",
    }

    try:
        for root, dirs, files in os.walk(repo_path):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if d not in excluded_dirs]

            for file in files:
                ext = Path(file).suffix.lower()
                if ext in extension_to_language:
                    lang = extension_to_language[ext]
                    file_counts[lang] = file_counts.get(lang, 0) + 1
    except PermissionError:
        pass

    # Determine languages (sorted by count)
    languages = sorted(file_counts.keys(), key=lambda x: file_counts[x], reverse=True)

    # Determine primary language
    primary_language = "Unknown"
    if languages:
        primary_language = languages[0]
    elif has_pyproject or has_requirements_txt or has_setup_py:
        primary_language = "Python"
    elif has_package_json:
        primary_language = "TypeScript" if has_tsconfig else "JavaScript"
    elif has_go_mod:
        primary_language = "Go"
    elif has_cargo_toml:
        primary_language = "Rust"
    elif has_gemfile:
        primary_language = "Ruby"

    return ProjectInfo(
        primary_language=primary_language,
        languages=languages,
        has_pyproject=has_pyproject,
        has_package_json=has_package_json,
        has_go_mod=has_go_mod,
        has_cargo_toml=has_cargo_toml,
        has_gemfile=has_gemfile,
        has_requirements_txt=has_requirements_txt,
        has_setup_py=has_setup_py,
        has_tsconfig=has_tsconfig,
        file_count=file_counts,
    )


def get_default_patterns(project_info: ProjectInfo) -> list[str]:
    """Get default file patterns based on detected project type.

    Args:
        project_info: Detected project information

    Returns:
        List of glob patterns for the project type
    """
    patterns = []

    # Add patterns based on detected languages
    language_patterns = {
        "Python": ["**/*.py"],
        "TypeScript": ["**/*.ts", "**/*.tsx"],
        "JavaScript": ["**/*.js", "**/*.jsx"],
        "Go": ["**/*.go"],
        "Rust": ["**/*.rs"],
        "Java": ["**/*.java"],
        "Kotlin": ["**/*.kt", "**/*.kts"],
        "Ruby": ["**/*.rb"],
        "PHP": ["**/*.php"],
        "C#": ["**/*.cs"],
        "Swift": ["**/*.swift"],
        "Scala": ["**/*.scala"],
        "C": ["**/*.c", "**/*.h"],
        "C++": ["**/*.cpp", "**/*.cc", "**/*.hpp", "**/*.cxx"],
        "C/C++": ["**/*.c", "**/*.h"],
    }

    for lang in project_info.languages[:5]:  # Top 5 languages
        if lang in language_patterns:
            patterns.extend(language_patterns[lang])

    # If no languages detected, use common defaults
    if not patterns:
        patterns = ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"]

    return list(dict.fromkeys(patterns))  # Remove duplicates while preserving order


def generate_reporc(project_info: ProjectInfo) -> str:
    """Generate .reporc content based on project type.

    Args:
        project_info: Detected project information

    Returns:
        YAML content for .reporc
    """
    patterns = get_default_patterns(project_info)
    patterns_yaml = "\n".join(f'  - "{p}"' for p in patterns)

    # Build detector config based on language
    detector_config = ""
    if project_info.primary_language == "Python":
        detector_config = """
# Python-specific detectors (enabled by default)
detectors:
  ruff:
    enabled: true
  bandit:
    enabled: true
  mypy:
    enabled: true
  pylint:
    enabled: true
    jobs: 4
"""
    elif project_info.primary_language in ("TypeScript", "JavaScript"):
        detector_config = """
# TypeScript/JavaScript-specific detectors
detectors:
  eslint:
    enabled: true
  tsc:
    enabled: true
"""

    return f"""# Repotoire Configuration
# Generated by 'repotoire init'
# See: https://docs.repotoire.com/configuration

ingestion:
  patterns:
{patterns_yaml}
  exclude_patterns:
    - "**/test_*.py"
    - "**/*_test.py"
    - "**/tests/**"
    - "**/__tests__/**"
    - "**/*.test.ts"
    - "**/*.test.tsx"
    - "**/*.spec.ts"
    - "**/*.spec.tsx"
    - "**/node_modules/**"
    - "**/vendor/**"
    - "**/dist/**"
    - "**/build/**"
    - "**/.venv/**"
    - "**/venv/**"
  max_file_size_mb: 10
  batch_size: 100

analysis:
  min_modularity: 0.3
  max_coupling: 5.0
{detector_config}
# Logging configuration
logging:
  level: INFO
  format: human
"""


def show_project_summary(project_info: ProjectInfo, repo_path: Path) -> None:
    """Display detected project summary.

    Args:
        project_info: Detected project information
        repo_path: Path to the repository
    """
    # Project type panel
    title = Text()
    title.append("📁 ", style="bold")
    title.append(str(repo_path.name), style="bold cyan")

    info_lines = []
    info_lines.append(f"[bold]Primary Language:[/bold] {project_info.primary_language}")

    if project_info.languages:
        langs_display = ", ".join(project_info.languages[:5])
        if len(project_info.languages) > 5:
            langs_display += f" (+{len(project_info.languages) - 5} more)"
        info_lines.append(f"[bold]Languages Detected:[/bold] {langs_display}")

    # Show file counts
    if project_info.file_count:
        total_files = sum(project_info.file_count.values())
        info_lines.append(f"[bold]Source Files:[/bold] {total_files:,}")

    # Show project markers
    markers = []
    if project_info.has_pyproject:
        markers.append("pyproject.toml")
    if project_info.has_package_json:
        markers.append("package.json")
    if project_info.has_go_mod:
        markers.append("go.mod")
    if project_info.has_cargo_toml:
        markers.append("Cargo.toml")
    if project_info.has_gemfile:
        markers.append("Gemfile")
    if project_info.has_tsconfig:
        markers.append("tsconfig.json")

    if markers:
        info_lines.append(f"[bold]Project Files:[/bold] {', '.join(markers)}")

    console.print(Panel("\n".join(info_lines), title=title, border_style="blue"))


def show_file_breakdown(project_info: ProjectInfo) -> None:
    """Display file count breakdown by language.

    Args:
        project_info: Detected project information
    """
    if not project_info.file_count:
        return

    table = Table(title="Files to Analyze", show_header=True, header_style="bold")
    table.add_column("Language", style="cyan")
    table.add_column("Files", justify="right")

    for lang, count in sorted(
        project_info.file_count.items(), key=lambda x: x[1], reverse=True
    ):
        table.add_row(lang, str(count))

    table.add_row("", "", style="dim")
    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{sum(project_info.file_count.values()):,}[/bold]",
    )

    console.print(table)
    console.print()


def show_next_steps(is_authenticated: bool, repo_path: Path) -> None:
    """Display next steps after initialization.

    Args:
        is_authenticated: Whether user is logged in
        repo_path: Path to the repository
    """
    console.print("\n[bold green]✓ Initialization complete![/bold green]\n")

    steps = []

    if not is_authenticated:
        steps.append(
            "[bold]1.[/bold] [cyan]repotoire login[/cyan] — Sync to cloud dashboard (optional)"
        )
        steps.append(
            "[bold]2.[/bold] [cyan]repotoire sync[/cyan] — Upload analysis to team dashboard"
        )
    else:
        steps.append(
            "[bold]1.[/bold] [cyan]repotoire sync[/cyan] — Upload analysis to cloud dashboard"
        )

    steps.append(
        "[bold]" + ("3" if not is_authenticated else "2") + ".[/bold] "
        "[cyan]repotoire ask \"What are the main modules?\"[/cyan] — Query with AI"
    )
    steps.append(
        "[bold]" + ("4" if not is_authenticated else "3") + ".[/bold] "
        "Set up CI: Add to your pipeline for continuous analysis"
    )

    console.print(Panel("\n".join(steps), title="[bold]Next Steps[/bold]", border_style="green"))

    # CI hint
    console.print("\n[dim]💡 For CI/CD integration:[/dim]")
    console.print("[dim]   repotoire analyze . --format sarif -o results.sarif[/dim]")
    console.print("[dim]   See: https://docs.repotoire.com/ci-cd[/dim]")


def show_already_initialized(repo_path: Path, is_authenticated: bool) -> None:
    """Display status for already initialized repository.

    Args:
        repo_path: Path to the repository
        is_authenticated: Whether user is logged in
    """
    repotoire_dir = repo_path / ".repotoire"

    console.print(
        Panel(
            f"[bold]This repository is already initialized.[/bold]\n\n"
            f"📁 Data directory: [cyan]{repotoire_dir}[/cyan]\n"
            f"🔑 Auth status: {'[green]Logged in[/green]' if is_authenticated else '[yellow]Local only[/yellow]'}",
            title="[bold blue]Repotoire Status[/bold blue]",
            border_style="blue",
        )
    )

    console.print("\n[bold]Available commands:[/bold]")
    console.print("  [cyan]repotoire analyze .[/cyan]     — Re-run analysis")
    console.print("  [cyan]repotoire ask \"...\"[/cyan]    — Query the codebase")
    if not is_authenticated:
        console.print("  [cyan]repotoire login[/cyan]         — Connect to cloud")
    else:
        console.print("  [cyan]repotoire sync[/cyan]          — Sync to cloud dashboard")
    console.print("  [cyan]repotoire findings[/cyan]      — View latest findings")


@handle_errors()
def run_init(
    repo_path: str,
    skip_auth: bool,
    skip_analysis: bool,
    force: bool,
    quiet: bool,
) -> None:
    """Run the init command logic.

    Args:
        repo_path: Path to the repository
        skip_auth: Skip authentication prompt
        skip_analysis: Skip running first analysis
        force: Force re-initialization even if already initialized
        quiet: Minimal output
    """
    path = Path(repo_path).resolve()

    if not path.exists():
        console.print(f"[red]✗[/red] Directory not found: {path}")
        raise click.Abort()

    if not path.is_dir():
        console.print(f"[red]✗[/red] Not a directory: {path}")
        raise click.Abort()

    # Check authentication status
    from repotoire.cli.auth import CLIAuth

    cli_auth = CLIAuth()
    is_authenticated = cli_auth.get_api_key() is not None

    repotoire_dir = path / ".repotoire"
    reporc_file = path / ".reporc"

    # Check if already initialized
    if repotoire_dir.exists() and not force:
        show_already_initialized(path, is_authenticated)
        return

    # Welcome banner
    if not quiet:
        console.print()
        console.print(
            Panel(
                "[bold]Welcome to Repotoire![/bold]\n\n"
                "Let's set up code health analysis for your project.\n"
                "This will create a [cyan].reporc[/cyan] config and run your first analysis.",
                title="🚀 [bold]repotoire init[/bold]",
                border_style="cyan",
            )
        )
        console.print()

    # Step 1: Detect project type
    if not quiet:
        console.print("[bold]Detecting project type...[/bold]")

    project_info = detect_project_type(path)

    if not quiet:
        show_project_summary(project_info, path)
        console.print()
        show_file_breakdown(project_info)

    # Step 2: Auth check (optional)
    if not skip_auth and not is_authenticated and not quiet:
        console.print(
            "[bold]Cloud Features[/bold] (optional): Team dashboards, historical trends, AI insights"
        )
        if Confirm.ask("Would you like to login to Repotoire Cloud?", default=False):
            try:
                api_key = cli_auth.login()
                is_authenticated = True
                console.print("[green]✓[/green] Logged in successfully!\n")
            except Exception as e:
                console.print(f"[yellow]⚠[/yellow] Login skipped: {e}")
                console.print("[dim]You can login later with 'repotoire login'[/dim]\n")

    # Step 3: Create .reporc
    if not quiet:
        console.print("[bold]Creating configuration...[/bold]")

    reporc_content = generate_reporc(project_info)

    if reporc_file.exists() and not force:
        if not quiet:
            console.print(f"[yellow]⚠[/yellow] Config file already exists: {reporc_file}")
            if not Confirm.ask("Overwrite existing .reporc?", default=False):
                console.print("[dim]Keeping existing config[/dim]")
                reporc_content = None

    if reporc_content:
        reporc_file.write_text(reporc_content)
        if not quiet:
            console.print(f"[green]✓[/green] Created [cyan]{reporc_file}[/cyan]")

    # Step 4: Create .repotoire directory if needed
    repotoire_dir.mkdir(exist_ok=True)

    # Add .repotoire to .gitignore if not already there
    gitignore = path / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".repotoire/" not in content and ".repotoire" not in content:
            with gitignore.open("a") as f:
                f.write("\n# Repotoire local data\n.repotoire/\n")
            if not quiet:
                console.print("[green]✓[/green] Added .repotoire/ to .gitignore")
    else:
        gitignore.write_text("# Repotoire local data\n.repotoire/\n")
        if not quiet:
            console.print("[green]✓[/green] Created .gitignore with .repotoire/")

    # Step 5: Run first analysis
    if not skip_analysis:
        if not quiet:
            console.print()
            console.print("[bold]Running first analysis...[/bold]")
            console.print("[dim]This may take a moment for larger codebases.[/dim]\n")

        # Import and run analyze command
        from repotoire.cli import analyze as analyze_cmd

        ctx = click.Context(analyze_cmd)
        ctx.ensure_object(dict)

        # Load config
        from repotoire.config import load_config

        try:
            config = load_config(config_file=str(reporc_file) if reporc_file.exists() else None)
        except Exception:
            from repotoire.config import FalkorConfig

            config = FalkorConfig()

        ctx.obj["config"] = config
        ctx.obj["tenant_id"] = None

        try:
            # Invoke analyze with minimal options
            ctx.invoke(
                analyze_cmd,
                repo_path=str(path),
                output=None,
                format="json",
                open_report=False,
                quiet=quiet,
                track_metrics=False,
                keep_metadata=True,
                parallel=True,
                workers=4,
                offline=not is_authenticated,
                fail_on_grade=None,
                disable_detectors=None,
                enable_detectors=None,
                insights=True,
                top=None,
                severity=None,
                changed=None,
            )
        except SystemExit:
            # analyze may call sys.exit on completion
            pass
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Analysis completed with warnings: {e}")

    # Step 6: Show next steps
    if not quiet:
        show_next_steps(is_authenticated, path)
