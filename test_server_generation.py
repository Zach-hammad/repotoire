"""Test MCP server generation end-to-end.

Tests the complete pipeline:
1. Pattern detection
2. Schema generation
3. Server generation
"""

import os
from pathlib import Path
from repotoire.graph import Neo4jClient
from repotoire.mcp import PatternDetector, SchemaGenerator, ServerGenerator

# Connect to Neo4j
password = os.getenv("REPOTOIRE_NEO4J_PASSWORD", "falkor-password")
client = Neo4jClient(uri="bolt://localhost:7688", password=password)

print("=" * 100)
print("🚀 MCP SERVER GENERATION - END-TO-END TEST")
print("=" * 100)

# Step 1: Detect patterns
print("\n📍 Step 1: Pattern Detection")
print("-" * 100)

detector = PatternDetector(client)

# Detect different types of patterns
routes = detector.detect_fastapi_routes()
commands = detector.detect_click_commands()
functions = detector.detect_public_functions(min_params=2, max_params=4)

# Limit for testing
routes = routes[:3]
commands = commands[:2]
functions = functions[:5]

all_patterns = routes + commands + functions

print(f"✅ Detected {len(all_patterns)} patterns:")
print(f"   - {len(routes)} FastAPI routes")
print(f"   - {len(commands)} Click commands")
print(f"   - {len(functions)} public functions")

# Step 2: Generate schemas
print("\n\n📋 Step 2: Schema Generation")
print("-" * 100)

generator = SchemaGenerator()
schemas = []

for pattern in all_patterns:
    schema = generator.generate_tool_schema(pattern)
    schemas.append(schema)
    print(f"   ✓ {schema['name']}: {schema['description'][:50]}...")

print(f"\n✅ Generated {len(schemas)} tool schemas")

# Step 3: Generate MCP server
print("\n\n🔧 Step 3: Server Generation")
print("-" * 100)

output_dir = Path("/tmp/generated_mcp_server")
server_gen = ServerGenerator(output_dir)

server_file = server_gen.generate_server(
    patterns=all_patterns,
    schemas=schemas,
    server_name="repotoire_mcp_server",
    repository_path="/home/zach/code/falkor"
)

print(f"✅ Generated MCP server at: {server_file}")

# Step 4: Validate generated code
print("\n\n✓ Step 4: Code Validation")
print("-" * 100)

# Read generated server
server_code = server_file.read_text()

# Check for key components
checks = [
    ("Server initialization", "server = Server("),
    ("Tool schemas", "TOOL_SCHEMAS = {"),
    ("List tools handler", "@server.list_tools()"),
    ("Call tool handler", "@server.call_tool()"),
    ("Main entry point", "def main():"),
    ("Stdio server", "stdio_server()"),
]

all_passed = True
for check_name, check_str in checks:
    if check_str in server_code:
        print(f"   ✅ {check_name}")
    else:
        print(f"   ❌ {check_name} - MISSING!")
        all_passed = False

# Show server stats
print(f"\n📊 Server Statistics:")
print(f"   Lines of code: {len(server_code.splitlines())}")
print(f"   File size: {len(server_code)} bytes")
print(f"   Tools registered: {len(schemas)}")

# Show preview of generated code
print("\n\n📄 Generated Code Preview (first 100 lines):")
print("-" * 100)
lines = server_code.splitlines()[:100]
for i, line in enumerate(lines, 1):
    print(f"{i:3d} | {line}")

if len(server_code.splitlines()) > 100:
    print(f"\n... ({len(server_code.splitlines()) - 100} more lines)")

# Show config file
config_file = output_dir / "config.py"
if config_file.exists():
    print("\n\n⚙️  Configuration File:")
    print("-" * 100)
    print(config_file.read_text())

print("\n\n" + "=" * 100)
if all_passed:
    print("✅ ALL CHECKS PASSED - Server generation successful!")
else:
    print("⚠️  SOME CHECKS FAILED - Review generated code")
print("=" * 100)

print(f"\n💡 Next Steps:")
print(f"   1. Review generated server: {server_file}")
print(f"   2. Install MCP SDK: pip install mcp")
print(f"   3. Test server: python {server_file}")
print(f"   4. Connect with MCP client (Claude Desktop, etc.)")

client.close()
