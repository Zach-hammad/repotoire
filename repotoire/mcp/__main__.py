"""CLI entry point for Repotoire API MCP Server.

Usage:
    python -m repotoire.mcp
    # or
    repotoire-mcp
"""

from repotoire.mcp.api_server import main

if __name__ == "__main__":
    main()
