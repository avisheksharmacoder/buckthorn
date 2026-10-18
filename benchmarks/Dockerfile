# ==============================================================================
# Buckthorn High-Performance Benchmark Dockerfile (Multi-Stage Build)
# ==============================================================================
# Optimized for minimum image size, SIMD hardware acceleration, and
# Link-Time Optimization (-flto) using CPython 3.12 on Debian Bookworm.
#
# Build context: Repository Root (.)
# Build command:
#   docker build -f benchmarks/Dockerfile -t buckthorn-bench:latest .
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Native C11 & Cython Builder
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Install C/C++ compilation toolchain and development headers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libuv1-dev \
    libssl-dev \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install modern Python build requirements
RUN pip install --no-cache-dir --upgrade pip setuptools wheel "cython>=3.0.0"

# Copy package manifests and source trees
COPY pyproject.toml setup.py ./
COPY scripts/ ./scripts/
COPY buckthorn/ ./buckthorn/

# Fetch SIMD C vendor dependencies (picohttpparser, rax, yyjson) and build
RUN bash scripts/fetch_vendor.sh && \
    pip install --no-cache-dir .

# ------------------------------------------------------------------------------
# Stage 2: Minimal Production Runtime
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Install minimal dynamic runtime shared libraries (libuv1, libssl3)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libuv1 \
    libssl3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy compiled Cython/C extensions and Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy benchmark application and traffic generation scripts
COPY benchmarks/ /app/benchmarks/
COPY benchmarks/app.py /app/app.py

# Standard performance environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BUCKTHORN_ACCESS_LOG=0

EXPOSE 8000

# Health check probe
HEALTHCHECK --interval=5s --timeout=2s --start-period=3s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# Default: Single-worker server (pure single-core benchmark configuration)
CMD ["python", "-m", "buckthorn.cli", "run", "app.py:app", "--host", "0.0.0.0", "--port", "8000"]
