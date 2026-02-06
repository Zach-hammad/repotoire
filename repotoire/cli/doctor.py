"""Environment diagnostics command for Repotoire CLI.

Provides comprehensive health checks for the local environment:
- Authentication status
- Local database health
- Optional dependencies (Rust parser)
- API key status
- Git availability
- Python version
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console

from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()


def _check_symbol(status: str) -> str:
    """Return the appropriate symbol for status."""
    if status == "ok":
        return "[green]✓[/]"
    elif status == "warning":
        return "[yellow]⚠[/]"
    else:  # error
        return "[red]✗[/]"


def check_authentication() -> Tuple[str, str]:
    """Check authentication status.
    
    Returns:
        Tuple of (status, message) where status is 'ok', 'warning', or 'error'
    """
    try:
        from repotoire.cli.auth import CLIAuth
        from repotoire.cli.credentials import mask_api_key
        
        cli_auth = CLIAuth()
        api_key = cli_auth.get_api_key()
        
        if not api_key:
            return ("warning", "Not logged in (local mode)")
        
        # Try to validate the key and get org info
        org_info = cli_auth._fetch_org_info(api_key)
        if org_info:
            org_slug = org_info.get("org_slug", "unknown")
            return ("ok", f"Logged in ({org_slug})")
        else:
            # Key exists but couldn't validate (might be offline)
            masked = mask_api_key(api_key)
            return ("ok", f"Logged in ({masked})")
            
    except Exception as e:
        logger.debug(f"Auth check failed: {e}")
        return ("warning", "Could not verify auth status")


def check_local_database(repository_path: str = ".") -> Tuple[str, str]:
    """Check local Kuzu database health.
    
    Returns:
        Tuple of (status, message)
    """
    try:
        repo_path = Path(repository_path).resolve()
        db_path = repo_path / ".repotoire" / "kuzu_db"
        
        if not db_path.exists():
            return ("warning", "Not initialized (run 'repotoire ingest' first)")
        
        # Calculate directory size
        total_size = sum(f.stat().st_size for f in db_path.rglob("*") if f.is_file())
        
        # Format size
        if total_size < 1024:
            size_str = f"{total_size}B"
        elif total_size < 1024 * 1024:
            size_str = f"{total_size / 1024:.1f}KB"
        else:
            size_str = f"{total_size / (1024 * 1024):.1f}MB"
        
        # Try to get node count
        node_count = None
        try:
            import kuzu
            db = kuzu.Database(str(db_path))
            conn = kuzu.Connection(db)
            
            # Count nodes across all tables
            result = conn.execute("MATCH (n) RETURN count(n) as cnt")
            while result.has_next():
                row = result.get_next()
                node_count = row[0]
                break
        except Exception as e:
            logger.debug(f"Could not count nodes: {e}")
        
        if node_count is not None:
            return ("ok", f"Healthy ({size_str}, {node_count:,} nodes)")
        else:
            return ("ok", f"Exists ({size_str})")
            
    except Exception as e:
        logger.debug(f"Database check failed: {e}")
        return ("error", f"Error checking database: {e}")


def check_rust_parser() -> Tuple[str, str]:
    """Check if Rust parser (repotoire_fast) is installed.
    
    Returns:
        Tuple of (status, message)
    """
    try:
        import repotoire_fast
        
        # Try to get version if available
        version = getattr(repotoire_fast, "__version__", None)
        if version:
            return ("ok", f"Installed v{version} (10x faster parsing)")
        return ("ok", "Installed (10x faster parsing)")
        
    except ImportError:
        return ("warning", "Not installed (using pure Python parser)")


def check_api_keys() -> Tuple[str, str]:
    """Check API key status for embeddings/LLM.
    
    Returns:
        Tuple of (status, message)
    """
    openai_key = os.getenv("OPENAI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    
    backends = []
    if openai_key:
        # Mask the key
        masked = openai_key[:7] + "..." + openai_key[-4:] if len(openai_key) > 15 else "***"
        backends.append(f"OpenAI ({masked})")
    if anthropic_key:
        masked = anthropic_key[:7] + "..." + anthropic_key[-4:] if len(anthropic_key) > 15 else "***"
        backends.append(f"Anthropic ({masked})")
    
    if backends:
        return ("ok", ", ".join(backends))
    else:
        return ("warning", "Not set (embeddings will use local model)")


def check_git() -> Tuple[str, str]:
    """Check Git availability.
    
    Returns:
        Tuple of (status, message)
    """
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # Parse version from "git version 2.43.0"
            version = result.stdout.strip().replace("git version ", "")
            return ("ok", f"Available (v{version})")
        else:
            return ("error", "Not working properly")
    except FileNotFoundError:
        return ("error", "Not installed")
    except subprocess.TimeoutExpired:
        return ("warning", "Timeout checking version")
    except Exception as e:
        return ("error", f"Error: {e}")


def check_python_version() -> Tuple[str, str]:
    """Check Python version.
    
    Returns:
        Tuple of (status, message)
    """
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    # Repotoire requires Python 3.11+
    if sys.version_info >= (3, 11):
        return ("ok", version)
    elif sys.version_info >= (3, 10):
        return ("warning", f"{version} (3.11+ recommended)")
    else:
        return ("error", f"{version} (requires 3.11+)")


def run_doctor(repository_path: str = ".") -> int:
    """Run all environment diagnostics.
    
    Args:
        repository_path: Path to repository for database checks
        
    Returns:
        Exit code (0 = all ok, 1 = warnings, 2 = errors)
    """
    console.print()
    
    checks = [
        ("Authentication", check_authentication),
        ("Local database", lambda: check_local_database(repository_path)),
        ("Rust parser", check_rust_parser),
        ("API keys", check_api_keys),
        ("Git", check_git),
        ("Python", check_python_version),
    ]
    
    has_warnings = False
    has_errors = False
    
    for name, check_fn in checks:
        try:
            status, message = check_fn()
            symbol = _check_symbol(status)
            console.print(f"{symbol} {name}: {message}")
            
            if status == "warning":
                has_warnings = True
            elif status == "error":
                has_errors = True
                
        except Exception as e:
            console.print(f"[red]✗[/] {name}: Error running check ({e})")
            has_errors = True
    
    console.print()
    
    if has_errors:
        return 2
    elif has_warnings:
        return 1
    return 0
