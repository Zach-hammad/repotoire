"""Quick test script for pattern detection."""

from repotoire.graph import FalkorDBClient
from repotoire.mcp.pattern_detector import PatternDetector
import os

# Connect to FalkorDB
password = os.getenv("FALKORDB_PASSWORD", "falkor-password")
client = FalkorDBClient(uri="bolt://localhost:7688", password=password)

# Create detector
detector = PatternDetector(client)

# Detect all patterns
print("🔍 Detecting patterns in Repotoire codebase...\n")

# Detect Click commands
commands = detector.detect_click_commands()
print(f"📋 Found {len(commands)} Click commands:")
for cmd in commands:
    print(f"  - {cmd.function_name}: {cmd.docstring[:60] if cmd.docstring else 'No description'}...")
    print(f"    Source: {cmd.source_file}:{cmd.line_number}")
    print(f"    Options: {len(cmd.options)}, Arguments: {len(cmd.arguments)}")
    print()

# Detect public functions
functions = detector.detect_public_functions(min_params=1, max_params=5)
print(f"\n⚙️  Found {len(functions)} public functions (sample):")
for func in functions[:10]:  # Show first 10
    print(f"  - {func.function_name}: {func.docstring[:60] if func.docstring else 'No description'}...")
    print(f"    Parameters: {', '.join(p.name for p in func.parameters)}")
    print()

# Detect FastAPI routes (we probably don't have any, but let's check)
routes = detector.detect_fastapi_routes()
print(f"\n🌐 Found {len(routes)} FastAPI routes")
if routes:
    for route in routes:
        print(f"  - {route.http_method.value} {route.path} -> {route.function_name}")

client.close()
print("\n✅ Pattern detection test complete!")
