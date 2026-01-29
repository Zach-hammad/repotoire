# Marketplace MCP Server Dockerfile
# Multi-tenant MCP server with Rust extension

FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies including Rust
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    pkg-config \
    libssl-dev \
    patchelf \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:$PATH"

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock README.md ./

# Copy Rust extension
COPY repotoire-fast/ ./repotoire-fast/

# Copy Python packages
COPY repotoire/ ./repotoire/

# Create virtual environment and install dependencies
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

# Install maturin and build
RUN uv pip install maturin --no-cache-dir
RUN maturin build --release --out dist

# Install CPU-only PyTorch first (saves ~2GB vs full CUDA version)
RUN uv pip install torch --index-url https://download.pytorch.org/whl/cpu --no-cache-dir

# Install our wheel (torch already satisfied by CPU version)
RUN uv pip install dist/*.whl --no-cache-dir
RUN uv pip install ".[lancedb]" --no-cache-dir --no-build-isolation

# Runtime stage
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install runtime dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r repotoire && useradd -r -g repotoire repotoire

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

# Copy the package with correct ownership
COPY --from=builder --chown=repotoire:repotoire /build/repotoire /app/repotoire

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER repotoire

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Run the HTTP server
CMD ["python", "-m", "repotoire.mcp_marketplace.http_server"]
