# Celery Worker Dockerfile for Repotoire
# Multi-stage build for smaller final image

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.12-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy only dependency files first (for caching)
COPY pyproject.toml uv.lock ./

# Create virtual environment and install dependencies (including lancedb for vector storage)
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install -e ".[worker,lancedb]" --no-cache-dir

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.12-slim as runtime

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r celery && useradd -r -g celery celery

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"

# Copy application code
COPY repotoire/ /app/repotoire/

# Create directories for temporary files
RUN mkdir -p /tmp/repotoire-clones && chown -R celery:celery /tmp/repotoire-clones
RUN mkdir -p /var/log/celery && chown -R celery:celery /var/log/celery

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Redis URL used by celery_app.py and progress tracking
ENV REDIS_URL=redis://redis:6379/0
ENV REPOTOIRE_CLONE_DIR=/tmp/repotoire-clones

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD celery -A repotoire.workers.celery_app inspect ping -d celery@$HOSTNAME || exit 1

# Switch to non-root user
USER celery

# Default command (can be overridden)
# Queues: default (hooks), analysis (full repo), analysis.priority (enterprise)
CMD ["celery", "-A", "repotoire.workers.celery_app", "worker", \
     "--loglevel=info", \
     "-Q", "default,analysis,analysis.priority", \
     "--concurrency=2"]
