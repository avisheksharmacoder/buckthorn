# =============================================================================
# Stage 1: C11 / Cython Native Compiler Stage
# =============================================================================
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    libuv1-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . /build

# 1. Fetch battle-tested C vendor sources (picohttpparser, Redis rax, yyjson)
RUN bash scripts/fetch_vendor.sh

# 2. Compile and install Buckthorn C/Cython core with -O3 -msse4.2 -flto
RUN pip install --no-cache-dir --upgrade pip setuptools wheel Cython && \
    pip install --no-cache-dir . --no-build-isolation

# =============================================================================
# Stage 2: Production Linux Runtime Image
# =============================================================================
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libuv1 \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/buckthorn /usr/local/bin/buckthorn

EXPOSE 8000
CMD ["buckthorn", "run", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
