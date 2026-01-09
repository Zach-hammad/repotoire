#!/usr/bin/env python3
"""
Export OpenAPI spec from FastAPI app without running the server.

Usage:
    python scripts/export_openapi.py > web/openapi.json
    python scripts/export_openapi.py --output web/openapi.json
"""

import argparse
import json
import sys
from pathlib import Path

# Add the parent directory to the path so we can import the app
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import only the API app, avoiding heavy dependencies
import os
os.environ.setdefault("REPOTOIRE_SKIP_HEAVY_IMPORTS", "1")

# Import the v1 API app which has all the routes
# The main app mounts v1_app at /api/v1, so we export from v1_app directly
try:
    from repotoire.api.v1 import v1_app as app
except ImportError as e:
    print(f"Error: Could not import v1_app: {e}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Export OpenAPI spec from FastAPI app")
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty print JSON (default: True)",
    )
    args = parser.parse_args()

    # Get the OpenAPI spec
    openapi_spec = app.openapi()

    # Format output
    if args.pretty:
        output = json.dumps(openapi_spec, indent=2)
    else:
        output = json.dumps(openapi_spec)

    # Write to file or stdout
    if args.output:
        Path(args.output).write_text(output)
        print(f"OpenAPI spec written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
