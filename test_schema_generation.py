"""Test script for MCP schema generation."""

import json
import os
from repotoire.graph import FalkorDBClient
from repotoire.mcp import PatternDetector, SchemaGenerator

# Connect to FalkorDB
password = os.getenv("FALKORDB_PASSWORD", "falkor-password")
client = FalkorDBClient(uri="bolt://localhost:7688", password=password)

# Detect patterns
detector = PatternDetector(client)
generator = SchemaGenerator()

print("🔍 Detecting patterns and generating MCP tool schemas...\n")

# Test FastAPI routes
print("=" * 70)
print("FastAPI Routes → MCP Tools")
print("=" * 70)
routes = detector.detect_fastapi_routes()
for route in routes[:3]:  # Show first 3
    schema = generator.generate_tool_schema(route)
    print(f"\n📍 Route: {route.http_method.value} {route.path}")
    print(f"   Function: {route.function_name}")
    print(f"   Tool Schema:")
    print(json.dumps(schema, indent=2))

# Test Click commands
print("\n" + "=" * 70)
print("Click Commands → MCP Tools")
print("=" * 70)
commands = detector.detect_click_commands()
for cmd in commands[:2]:  # Show first 2
    schema = generator.generate_tool_schema(cmd)
    print(f"\n🔨 Command: {cmd.command_name}")
    print(f"   Options: {len(cmd.options)}, Arguments: {len(cmd.arguments)}")
    print(f"   Tool Schema:")
    print(json.dumps(schema, indent=2))

# Test public functions
print("\n" + "=" * 70)
print("Public Functions → MCP Tools")
print("=" * 70)
functions = detector.detect_public_functions(min_params=2, max_params=3)
for func in functions[:3]:  # Show first 3
    schema = generator.generate_tool_schema(func)
    print(f"\n⚙️  Function: {func.function_name}")
    print(f"   Parameters: {', '.join(p.name for p in func.parameters)}")
    print(f"   Tool Schema:")
    print(json.dumps(schema, indent=2))

client.close()
print("\n✅ Schema generation test complete!")
