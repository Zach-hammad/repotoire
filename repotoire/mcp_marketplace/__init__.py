"""Marketplace MCP Server - serves user's installed marketplace assets via MCP protocol.

This is a multi-tenant MCP server that:
- Authenticates users via Repotoire API key
- Exposes installed skills as MCP tools
- Exposes commands/prompts as prompt templates
- Exposes styles as system context

Deployed on Fly.io as a shared service.
"""

from .server import create_server, run_server

__all__ = ["create_server", "run_server"]
