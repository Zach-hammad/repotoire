# Repotoire Enterprise - E2B Sandbox Template
#
# Premium template with Rust extensions for Pro/Enterprise tiers.
# Includes all analysis tools PLUS repotoire_fast for faster analysis.
#
# Build time: ~8-10 minutes (Rust compilation)
# Image size: ~1.2GB (includes Rust toolchain)

FROM python:3.11-slim AS builder

# Install Rust toolchain for building repotoire_fast
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.cargo/bin:${PATH}"

# Install maturin for building Python wheels from Rust
RUN pip install --no-cache-dir maturin

# Copy and build repotoire_fast
WORKDIR /build
COPY repotoire-fast/ ./repotoire-fast/
COPY repotoire_fast/ ./repotoire_fast/

WORKDIR /build/repotoire-fast
RUN maturin build --release --strip

# ============================================
# Final image - lean runtime
# ============================================
FROM python:3.11-slim

LABEL maintainer="Repotoire <hello@repotoire.dev>"
LABEL version="1.0.0"
LABEL description="Enterprise sandbox with Rust extensions for Pro/Enterprise tiers"
LABEL tier="enterprise"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Python analysis tools
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
    # Testing
    pytest \
    pytest-cov \
    coverage \
    # Data science (for advanced analysis)
    numpy \
    && rm -rf ~/.cache/pip /root/.cache

# Install jscpd via npm
RUN npm install -g jscpd --silent \
    && npm cache clean --force \
    && rm -rf /root/.npm

# Copy pre-built repotoire_fast wheel from builder
COPY --from=builder /build/repotoire-fast/target/wheels/*.whl /tmp/

# Install repotoire_fast
RUN pip install --no-cache-dir /tmp/*.whl \
    && rm -rf /tmp/*.whl

# Verify all tools are installed
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
    && python -c "import repotoire_fast; print('repotoire_fast OK')" \
    && echo "All tools verified successfully!"

WORKDIR /code

CMD ["/bin/bash"]
