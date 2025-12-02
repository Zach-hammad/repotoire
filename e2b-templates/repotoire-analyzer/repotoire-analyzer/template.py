"""Repotoire Analyzer - E2B Sandbox Template.

This template pre-installs all analysis tools used by Repotoire to dramatically
reduce sandbox startup time from ~30-60s (installing tools) to ~5-10s (template ready).

Tools included:
  - ruff: Fast Python linter (400+ rules)
  - bandit: Security vulnerability scanner
  - pylint: Code analysis tool
  - mypy: Static type checker
  - radon: Cyclomatic complexity metrics
  - vulture: Dead code detector
  - pytest: Test runner (for fix validation)
"""

from e2b import Template

template = (
    Template()
    .from_image("python:3.11-slim")
    # Install system dependencies (use sudo for apt-get as E2B runs as non-root)
    .run_cmd("sudo apt-get update && sudo apt-get install -y --no-install-recommends git curl ca-certificates && sudo apt-get clean && sudo rm -rf /var/lib/apt/lists/*")
    # Install Python analysis tools
    .run_cmd("pip install --no-cache-dir ruff pylint mypy bandit radon vulture pytest pytest-cov coverage")
    # Verify installations
    .run_cmd("ruff --version && bandit --version && pylint --version && mypy --version && radon --version && vulture --version && pytest --version")
    # Create working directory
    .run_cmd("sudo mkdir -p /code && sudo chown -R user:user /code")
    .set_workdir("/code")
)
