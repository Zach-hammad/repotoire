# Repotoire Analyzer - E2B Sandbox Template
#
# This image pre-installs all analysis tools used by Repotoire for code health analysis.
# Pre-installing tools reduces sandbox startup from ~30-60s to ~5-10s.
#
# Tools included:
#   - ruff: Fast Python linter (400+ rules)
#   - bandit: Security vulnerability scanner
#   - pylint: Code analysis tool
#   - mypy: Static type checker
#   - semgrep: Advanced security/code patterns
#   - radon: Cyclomatic complexity metrics
#   - vulture: Dead code detector
#   - jscpd: Copy/paste detector (requires Node.js)
#   - pytest: Test runner (for fix validation)
#
# Image size target: <800MB (semgrep is ~200MB alone)

FROM python:3.11-slim

LABEL maintainer="Repotoire <hello@repotoire.dev>"
LABEL version="1.0.0"
LABEL description="Pre-configured sandbox for Repotoire code analysis"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies in one layer
# - git: For repository operations
# - curl: For downloading Node.js setup script
# - Node.js 18: Required for jscpd (copy/paste detection)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Python analysis tools
# Grouped by function for clarity, installed in one layer for efficiency
RUN pip install --no-cache-dir \
    # Linting
    ruff \
    pylint \
    # Type checking
    mypy \
    # Security
    bandit \
    semgrep \
    # Metrics & dead code
    radon \
    vulture \
    # Testing (for fix validation)
    pytest \
    pytest-cov \
    coverage \
    && rm -rf ~/.cache/pip /root/.cache

# Install jscpd via npm for copy/paste detection
RUN npm install -g jscpd --silent \
    && npm cache clean --force \
    && rm -rf /root/.npm

# Verify all tools are installed correctly
# This will fail the build if any tool is missing
RUN echo "Verifying tool installations..." \
    && ruff --version \
    && bandit --version \
    && pylint --version \
    && mypy --version \
    && semgrep --version \
    && radon --version \
    && vulture --version \
    && jscpd --version \
    && pytest --version \
    && echo "All tools verified successfully!"

# Create working directory
WORKDIR /code

# Default command (can be overridden)
CMD ["/bin/bash"]
