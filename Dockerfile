# syntax=docker/dockerfile:1.7

# ==============================================================================
# Stage 1: C11 + Cython Builder & Ahead-of-Time App Transpiler
# ==============================================================================
FROM python:3.13-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
     PYTHONDONTWRITEBYTECODE=1 \
     PYTHONUNBUFFERED=1 \
     PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libuv1-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# 1. Install build toolchain and compile Buckthorn C11/Cython engine
COPY pyproject.toml setup.py README.md ./
COPY buckthorn ./buckthorn

# Set BUCKTHORN_PORTABLE_WHEEL=1 if building an image in CI to run across varied CPU models,
# or leave unset ("0") if building directly on the target production host for -march=native.
ARG BUCKTHORN_PORTABLE_WHEEL=0
ENV BUCKTHORN_PORTABLE_WHEEL=${BUCKTHORN_PORTABLE_WHEEL}

RUN pip install --upgrade pip setuptools wheel "Cython>=3.0.10" \
    && pip install . --no-build-isolation

# 2. Ahead-of-Time (AOT) compile the user application module into a native .so binary
COPY main.py ./
RUN buckthorn build main.py --prod --out-dir /build/app_dist \
    && rm -rf /build/app_dist/build_tmp /build/app_dist/*.c /build/app_dist/*.pyx

# 3. Strip build-only Python packages from the runtime virtualenv
RUN pip uninstall -y Cython setuptools wheel pip


# ==============================================================================
# Stage 2: Minimal Hardened Production Runtime
# ==============================================================================
FROM python:3.13-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install only the runtime C shared objects required by buckthorn._core.so
RUN apt-get update && apt-get install -y --no-install-recommends \
    libuv1 \
    libssl3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 buckthorn \
    && useradd -u 10001 -g buckthorn -s /usr/sbin/nologin -M buckthorn

WORKDIR /app

# Copy pre-compiled virtualenv and AOT-compiled application .so
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/app_dist/ /app/
COPY --from=builder /build/main.py /app/main.py

USER buckthorn:buckthorn
EXPOSE 8000

# Zero-dependency socket healthcheck hitting Buckthorn's C router
HEALTHCHECK --interval=10s --timeout=2s --start-period=3s --retries=3 \
    CMD python -c "import socket; s = socket.create_connection(('127.0.0.1', 8000), 1.5); s.sendall(b'GET /openapi.json HTTP/1.1\r\nHost: localhost\r\n\r\n'); ok = b'200 OK' in s.recv(128); s.close(); raise SystemExit(0 if ok else 1)"

# Exec form ensures Buckthorn's master supervisor is PID 1 and handles SIGTERM
CMD ["buckthorn", "run", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
