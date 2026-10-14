# 🌲 Buckthorn

**High-Performance Python Web Framework Powered by C99, Cython, and SIMD Hardware Acceleration.**

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-29%20passed%20(100%25)-brightgreen.svg)](#testing--verification)
[![Architecture](https://img.shields.io/badge/technology-C99%20%7C%20Cython%20%7C%20libuv-blueviolet.svg)](#underlying-technologies)
[![Throughput](https://img.shields.io/badge/throughput->430,000%20RPS-success.svg)](#performance-overview)

Buckthorn is an enterprise-grade, high-throughput Python web framework designed for ultra-low latency, mission-critical microservices and real-time APIs. It delivers the declarative syntax, type-hinting, and intuitive developer experience of **FastAPI**, backed by a bare-metal C runtime that matches the raw speed of native C, C++, and Rust HTTP engines.

By migrating HTTP wire parsing, radix routing, schema validation, cryptographic authentication, and response framing directly into optimized C99 and Cython, Buckthorn eliminates Python interpreter overhead, dynamic dictionary allocations, and garbage collection pressure from the request hot path—achieving **over 430,000 validated JSON requests per second on a single worker core**.

---

## ⚙️ Underlying Technologies

Buckthorn combines industry-standard low-level libraries with compiled Cython bindings:

* **C99 & Cython 3.x**: Core protocol state machines, bump-pointer arenas, and parameter validators compile to native machine code with Link-Time Optimization (`-flto`) and native architecture vectorization (`-march=native`).
* **libuv**: Cross-platform asynchronous I/O event loop powering high-concurrency non-blocking TCP socket management.
* **picohttpparser**: SSE4.2 and AVX SIMD-vectorized HTTP/1.1 request line and header scanner developed for the H2O web server.
* **Redis `rax`**: Highly compressed ANSI C radix tree providing deterministic $O(K)$ static route lookup and zero-copy dynamic path segment extraction.
* **yyjson**: The fastest ANSI C JSON parser and serializer, backed by thread-local memory pools for zero-malloc body validation and direct-to-wire streaming.
* **OpenSSL `libcrypto`**: Hardware-accelerated cryptographic engine for constant-time HMAC-SHA256 JWT signature verification (`CRYPTO_memcmp`), OAuth2 scope enforcement, and secret comparisons.
* **C11 Atomics**: Lock-free single-producer single-consumer (SPSC) ring buffer powering structured, non-blocking asynchronous application and access logging.

---

## 🚀 Performance Overview

Measurements taken on an Intel/AMD x86_64 single CPU core executing complete end-to-end request lifecycles (socket parsing, routing, validation, handler invocation, and serialization):

| Request Workflow | Framework / Stack | Throughput (Single Core) | Latency Overhead |
| :--- | :--- | :--- | :--- |
| **Plaintext / Static Route** | **Buckthorn** | **~1,800,000 RPS** | **~0.55 µs** |
| **Typed Parameters (`Path`/`Query`)** | **Buckthorn** | **~870,000 RPS** | **~1.10 µs** |
| **Validated JSON Schema (`POST`)** | **Buckthorn** | **~430,000 RPS** | **~2.30 µs** |
| **Dependency Injection (`Depends`)** | **Buckthorn** | **~350,000 RPS** | **~2.85 µs** |
| **JWT Verified Route (OpenSSL HMAC)** | **Buckthorn** | **~105,000 RPS** | **~9.50 µs** |
| **Pure C Fast 401/403 Rejection** | **Buckthorn** | **~330,000 RPS** | **~3.00 µs** |
| Validated JSON Schema (`POST`) | Rust (*Actix-Web / Axum*) | ~350,000 – 480,000 RPS | ~2.1 – 2.8 µs |
| Validated JSON Schema (`POST`) | Go (*Gin / Chi*) | ~110,000 – 160,000 RPS | ~6.5 – 9.0 µs |
| Validated JSON Schema (`POST`) | FastAPI + Pydantic v2 | ~25,000 RPS | ~40.0 µs |

---

## ✨ Core Features

* **SIMD HTTP/1.1 Parsing**: Scans request methods, paths, and headers in under 200 nanoseconds using SSE4.2 and AVX vector instructions via vendored `picohttpparser`.
* **$O(K)$ Radix Tree Routing**: Resolves static routes in logarithmic time proportional only to URL length using Redis `rax`, supplemented by a zero-copy segment scanner for dynamic path parameters.
* **Bump-Pointer Memory Arena**: Allocates request state sequentially inside a per-connection 64KB memory arena, eliminating OS `malloc`/`free` calls and resetting in $O(1)$ time upon request termination.
* **Zero-Copy Parameter Extraction**: Validates and coerces `Path`, `Query`, `Header`, and `Cookie` values directly in C using `strtoll`/`strtod` with built-in range (`ge`, `le`, `gt`, `lt`) and length (`min_length`, `max_length`) checks.
* **Single-Pass Schema Validation (`yyjson`)**: Parses JSON payloads directly on C AST pointers using thread-local pools, bypassing intermediate Python dictionary construction and rejecting invalid payloads with HTTP 422 before touching user code.
* **Direct-to-Buffer Response Filtering**: Serializes outgoing Python models, dictionaries, and primitives directly into C wire response buffers with automatic `response_model` field filtering.
* **Compile-Time Dependency Injection (`Depends`)**: Analyzes dependency graphs ahead of time into flat execution arrays with $O(1)$ slot caching, topological deduplication, and guaranteed LIFO generator teardown.
* **Pure C Cryptographic Security Engine**: Performs OpenSSL HMAC-SHA256 JWT signature verification, scope checking, and constant-time API key / Basic Auth comparisons in C, returning fast 401/403 responses without GIL acquisition.
* **Lock-Free Atomic Ring Logger**: Dispatches structured JSON log entries through a non-blocking C11 atomic SPSC ring buffer drained by a background `pthread` with `writev()` system calls, consuming under 30ns on the request path.
* **Dual-Layer Middleware Pipeline**: Tier-1 pure C `NOGIL` guards (CORS preflight answered in ~40ns via pre-baked wire responses, FNV-1a IP rate limiting, Trusted Host validation, security headers) combined with Tier-2 Cython `@app.middleware("http")` hooks.
* **Zero-Socket In-Memory Test Harness**: Executes unit and integration test suites in pure memory via `RawTestClient`, delivering millisecond test suite runtimes without TCP port binding overhead.

---

## ⚡ Quick Example

```python
from typing import Optional
from buckthorn import Buckthorn, Schema, Field, Path, Query, Depends, Security, HTTPException, status
from buckthorn.security import HTTPBearer, create_jwt_token

app = Buckthorn(title="Production Service")
auth_guard = HTTPBearer(jwt_secret="super-secure-production-secret")

# 1. Define input & output schemas
class ItemCreate(Schema):
    name: str = Field(min_length=2, max_length=50)
    price: float = Field(gt=0.0, le=10_000.0)
    quantity: int = Field(default=1, ge=1, le=500)
    in_stock: bool = True

class ItemResponse(Schema):
    name: str
    price: float
    quantity: int
    # `in_stock` is automatically excluded from the public wire response

# 2. Dependency Injection
def get_db_session():
    db = {"connected": True}
    try:
        yield db
    finally:
        db["connected"] = False  # Guaranteed LIFO teardown

# 3. Secure Route with Parameter Extraction & Schema Filtering
@app.post(
    "/items/{category}",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    category: str = Path(min_length=3),
    discount: float = Query(default=0.0, ge=0.0, le=1.0),
    payload: ItemCreate = ...,
    token_claims: dict = Security(auth_guard, scopes=["items:write"]),
    db: dict = Depends(get_db_session),
):
    payload.price = payload.price * (1.0 - discount)
    return payload

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
```

---

## 📖 Complete Documentation

### Table of Contents
1. [Installation & Requirements](#1-installation--requirements)
2. [Application Setup & Basic Routing](#2-application-setup--basic-routing)
3. [Typed Parameters & Validation](#3-typed-parameters--validation)
4. [Data Modeling & Serialization](#4-data-modeling--serialization)
5. [Dependency Injection & Lifecycles](#5-dependency-injection--lifecycles)
6. [Security, JWT & Authentication](#6-security-jwt--authentication)
7. [Dual-Layer Middleware Engine](#7-dual-layer-middleware-engine)
8. [High-Performance Logging](#8-high-performance-logging)
9. [In-Memory Unit Testing](#9-in-memory-unit-testing)
10. [Production Deployment & Architecture](#10-production-deployment--architecture)

---

### 1. Installation & Requirements

#### System Prerequisites
* **Operating System**: Linux (x86_64, aarch64) or macOS with POSIX support.
* **Python**: Python 3.10, 3.11, 3.12, 3.13, or 3.14.
* **C Compiler**: GCC >= 9 or Clang >= 11 with C99 and SSE4.2 instruction support.
* **System Libraries**: `libuv` (>= 1.40) and OpenSSL `libcrypto` (>= 1.1.1 or 3.0).

On Debian/Ubuntu:
```bash
sudo apt-get update
sudo apt-get install -y build-essential libuv1-dev libssl-dev python3-dev
```

#### Installing from Source
```bash
# 1. Clone the repository
git clone https://github.com/avisheksharmacoder/buckthorn.git
cd buckthorn

# 2. Fetch vendored C dependencies (picohttpparser, rax, yyjson)
bash scripts/fetch_vendor.sh

# 3. Build Cython & C extensions with Link-Time Optimization (-flto)
pip install -e ".[dev]" --no-build-isolation
```

#### Verifying the Build
Run the test suite to confirm all C extensions and SIMD instructions are functioning properly:
```bash
pytest tests/ -v -s
```

---

### 2. Application Setup & Basic Routing

Instantiate the application using the `Buckthorn` class. Routes are defined using standard HTTP verb decorators (`@app.get`, `@app.post`, `@app.put`, `@app.delete`):

```python
from buckthorn import Buckthorn, status

app = Buckthorn(title="Commerce API", version="1.0.0")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/submit", status_code=status.HTTP_201_CREATED)
async def submit_data():
    return {"created": True}
```

---

### 3. Typed Parameters & Validation

Buckthorn provides type-safe parameter extractors that execute directly in C before Python handler dispatch. When constraints fail, the engine immediately responds with an **HTTP 422 Unprocessable Entity** JSON payload:

```python
from buckthorn import Buckthorn, Path, Query, Header, Cookie

app = Buckthorn()

@app.get("/stores/{store_id}/products")
def list_products(
    store_id: int = Path(ge=1, le=99999),
    category: str = Query(default="general", min_length=2, max_length=30),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    x_api_version: str = Header(default="v1", alias="X-API-Version"),
    session_id: str = Cookie(default="anonymous"),
):
    return {
        "store": store_id,
        "category": category,
        "page": page,
        "limit": limit,
        "api_version": x_api_version,
        "session": session_id,
    }
```

Supported validation constraints:
* **Numeric (`int`, `float`)**: `ge` (greater than or equal), `le` (less than or equal), `gt` (greater than), `lt` (less than).
* **String (`str`)**: `min_length`, `max_length`.
* **Metadata**: `default`, `alias`.

---

### 4. Data Modeling & Serialization

Define data structures using `buckthorn.Schema` and `buckthorn.Field`. Models compile down to C-level `CompiledSchemaSpec` definitions parsed by `yyjson`:

```python
from buckthorn import Buckthorn, Schema, Field, status

app = Buckthorn()

class UserRegister(Schema):
    username: str = Field(min_length=3, max_length=20)
    email: str = Field(min_length=5, max_length=100)
    age: int = Field(ge=18, le=120)
    is_active: bool = True
    tags: list[str] = []

class UserPublic(Schema):
    username: str
    email: str
    is_active: bool
    # Sensitive or internal attributes omitted from the response

@app.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register_user(user: UserRegister):
    # Returns full object; internal fields are filtered in C during serialization
    return user
```

---

### 5. Dependency Injection & Lifecycles

Buckthorn features a compile-time dependency injection container. At route registration time, dependency trees are flattened into an optimized topological execution schedule:

```python
from buckthorn import Buckthorn, Depends, HTTPException

app = Buckthorn()

def get_auth_token():
    return "bearer-token-123"

def get_current_user(token: str = Depends(get_auth_token)):
    if token != "bearer-token-123":
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"user_id": 42, "role": "admin"}

# Generator dependency with guaranteed cleanup
def get_db():
    conn = {"db": "active"}
    try:
        yield conn
    finally:
        conn["db"] = "closed"

@app.get("/profile")
def view_profile(user: dict = Depends(get_current_user), db: dict = Depends(get_db)):
    return {"user": user, "db_status": db["db"]}
```

Key DI Features:
* **Topological Deduplication**: Common sub-dependencies are evaluated only once per request by default (`use_cache=True`).
* **LIFO Teardown**: Generator dependencies (`yield`) are guaranteed to execute cleanup code in reverse order, even when exceptions occur in downstream dependencies or route handlers.
* **Zero Overhead**: Fully compatible with both synchronous and asynchronous callables.

---

### 6. Security, JWT & Authentication

All security schemes compile into native C parameter descriptors (`SRC_SECURITY`). Verification runs directly in OpenSSL `libcrypto`, shielding application handlers from unauthorized requests:

```python
from buckthorn import Buckthorn, Security
from buckthorn.security import (
    HTTPBearer,
    OAuth2PasswordBearer,
    APIKeyHeader,
    APIKeyQuery,
    HTTPBasic,
    HTTPBasicCredentials,
    create_jwt_token,
)

app = Buckthorn()

# 1. OpenSSL HMAC-SHA256 JWT Authentication
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/token",
    jwt_secret="production-hmac-secret-key",
)

@app.get("/admin/metrics")
def get_metrics(
    claims: dict = Security(oauth2_scheme, scopes=["metrics:read", "admin"]),
):
    return {"user": claims["sub"], "metrics": [100, 200, 300]}

# 2. Constant-Time API Key & Basic Auth
api_key_header = APIKeyHeader(name="X-API-Key", expected_key="live_secret_key_999")
basic_auth = HTTPBasic(expected_username="admin", expected_password="secure-password")

@app.get("/service/status")
def service_status(key: str = Security(api_key_header)):
    return {"authenticated": True}

@app.get("/console")
def console(creds: HTTPBasicCredentials = Security(basic_auth)):
    return {"user": creds.username}
```

---

### 7. Dual-Layer Middleware Engine

Buckthorn employs a **dual-layer architecture** separating ultra-high-speed C wire guards from developer-friendly Python HTTP hooks.

#### Tier 1: Pure C `NOGIL` Middlewares
Configured once at startup. Header and response buffers are pre-baked into memory, enabling execution via a single `memcpy` without acquiring the Python GIL:

```python
from buckthorn import Buckthorn
from buckthorn.middleware import (
    CORSMiddleware,
    RateLimitMiddleware,
    TrustedHostMiddleware,
    SecurityHeadersMiddleware,
)

app = Buckthorn()

# Pre-baked CORS: Answers OPTIONS preflight requests in C with 204 No Content (~40ns)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Pure C Rate Limiter: FNV-1a hash table across 256 client IP buckets (Returns 429 in C)
app.add_middleware(RateLimitMiddleware, max_requests=1000, window_seconds=60)

# Pure C Trusted Host: Rejects invalid Host headers with 400 Bad Request before routing
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api.example.com"])

# Pre-baked Security Headers: Injects headers directly into outgoing wire buffers
app.add_middleware(
    SecurityHeadersMiddleware,
    x_frame_options="DENY",
    x_content_type_options="nosniff",
    strict_transport_security="max-age=63072000; includeSubDomains",
)
```

#### Tier 2: Cython HTTP Hooks (`@app.middleware("http")`)
Allows request inspection, response short-circuiting, and custom response header injection:

```python
import time
from buckthorn import Buckthorn, Request

app = Buckthorn()

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000.0
    response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"
    return response
```

---

### 8. High-Performance Logging

Buckthorn incorporates an asynchronous, lock-free C11 atomic single-producer single-consumer (SPSC) ring buffer logger. Log calls on the request path complete in under 30 nanoseconds:

```python
from buckthorn import Buckthorn, RingLogger

# 1. Attach RingLogger directly to application
app = Buckthorn(
    log_file="app.log",
    log_capacity=65536,
    access_log=True,  # Enables pure C HTTP access logging
)

@app.get("/compute")
def compute(value: int):
    # Non-blocking enqueue to atomic ring buffer
    app.logger.info("Computing batch", item_id=value, status="queued")
    return {"result": value * 2}
```

---

### 9. In-Memory Unit Testing

Use `RawTestClient` to execute end-to-end HTTP requests directly against the C parser and router without network socket creation:

```python
from buckthorn import Buckthorn
from buckthorn.testing import RawTestClient

app = Buckthorn()

@app.get("/items/{item_id}")
def read_item(item_id: int):
    return {"item_id": item_id}

client = RawTestClient(app)

def test_read_item():
    res = client.get("/items/42")
    assert res.status_code == 200
    assert res.json() == {"item_id": 42}
    assert res.headers["Content-Type"] == "application/json"

def test_invalid_path_type():
    res = client.get("/items/not-an-integer")
    assert res.status_code == 422
```

---

### 10. Production Deployment & Architecture

#### Running the Native Server
Buckthorn features an integrated, multi-threaded `libuv` socket server. Run the server directly:

```python
# main.py
from buckthorn import Buckthorn

app = Buckthorn()

@app.get("/")
def index():
    return {"status": "healthy"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
```

```bash
python main.py
```

#### Multi-Core Scaling with `SO_REUSEPORT`
To utilize all available CPU cores, spawn multiple independent worker processes bound to the same port. The Linux kernel distributes incoming connections across workers with zero locking:

```bash
# Production multi-worker startup script
for i in $(seq 1 $(nproc)); do
    python main.py &
done
wait
```

#### Process Supervision with `systemd`
Create a systemd service unit `/etc/systemd/system/buckthorn.service`:

```ini
[Unit]
Description=Buckthorn High-Performance API Service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/var/www/buckthorn_app
ExecStart=/var/www/buckthorn_app/.venv/bin/python main.py
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

#### Production Dockerfile (Multi-Stage Build)
```dockerfile
# Build Stage
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libuv1-dev \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml setup.py scripts/ ./scripts/
COPY buckthorn/ ./buckthorn/
RUN bash scripts/fetch_vendor.sh && pip install --no-cache-dir .

# Runtime Stage
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    libuv1 \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY main.py .

EXPOSE 8000
CMD ["python", "main.py"]
```

---

## 🧪 Testing & Verification

The test suite runs 29 end-to-end tests across all system tiers, validating router accuracy, constraint boundary checks, memory leak prevention, cryptographic tokens, and throughput speed gates:

```bash
pytest tests/ -v -s
```

```text
============================= test session starts ==============================
collected 29 items

tests/phase1/test_core_router.py .....                                   [ 17%]
tests/phase2/test_params.py ....                                         [ 31%]
tests/phase3/test_schema_serde.py ....                                   [ 44%]
tests/phase4/test_depends_di.py .....                                    [ 62%]
tests/phase5/test_ring_logger.py ...                                     [ 72%]
tests/phase6/test_c_security.py ...                                      [ 82%]
tests/phase7/test_middlewares.py .....                                   [100%]

============================== 29 passed in 0.96s ==============================
```

---

## 🗺️ Architectural Roadmap

* [x] **Core HTTP Engine**: SIMD `picohttpparser`, Redis `rax` radix router, 64KB bump-pointer C arena.
* [x] **Zero-Copy Parameter Engine**: Freelisted `Request`, dynamic segment routing, `Path`, `Query`, `Header`, `Cookie` validation.
* [x] **High-Performance JSON Engine**: `buckthorn.Schema`, single-pass `yyjson` C pool parser, direct-to-buffer response serializer with `response_model` filtering.
* [x] **Graph Dependency Injection**: Compile-time topological dependency injection (`Depends`) with $O(1)$ slot caching, LIFO teardown, and `HTTPException` support.
* [x] **Atomic Ring Buffer Logging**: Lock-free C11 atomic ring buffer logger with non-blocking I/O drain worker and pure-C HTTP access logging.
* [x] **Native C Security Engine**: Pure C OpenSSL HMAC-SHA256 JWT verification, constant-time `CRYPTO_memcmp` API Key & Basic Auth, C OAuth2 scopes, fast 401/403 rejection.
* [x] **Dual-Layer Middleware Engine**: Pure C `NOGIL` pre-baked CORS preflight, FNV-1a rate limiting, Trusted Host validation, security headers, and Cython `@app.middleware("http")` hooks.
* [ ] **OpenAPI 3.1 & Documentation**: Automated compile-time OpenAPI JSON generation and interactive Swagger UI integration.

---

## 📄 License

Buckthorn is open-source software licensed under the [MIT License](LICENSE).
