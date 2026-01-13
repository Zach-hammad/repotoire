# MCP Server Configuration
# Server: repotoire_mcp_server
import os

SERVER_NAME = "repotoire_mcp_server"
REPOSITORY_PATH = os.getenv("REPOSITORY_PATH", os.getcwd())

# Transport options
TRANSPORT = "stdio"  # or "http"
HTTP_PORT = 8000  # if using HTTP transport
